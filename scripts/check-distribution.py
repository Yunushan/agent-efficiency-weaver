#!/usr/bin/env python3
"""Offline source and wheel distribution verifier."""
import argparse
import ast
import hashlib
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unicodedata
import venv
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
INVALID_WINDOWS_CHARS = frozenset('<>:"|?*')
RESERVED_WINDOWS_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "clock$",
        "conin$",
        "conout$",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        *(f"{prefix}{number}" for prefix in ("com", "lpt") for number in ("¹", "²", "³")),
    }
)
SENSITIVE_PARTS = {
    ".git",
    ".github",
    ".gitlab",
    ".kilo",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "benchmarks",
    "build",
    "credentials",
    "dist",
    "docs",
    "generated",
    "node_modules",
    "plans",
    "private",
    "secrets",
    "tests",
    "venv",
}
SENSITIVE_NAMES = {
    ".env",
    ".gitattributes",
    ".gitignore",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
}
SENSITIVE_SUFFIXES = {".key", ".pem", ".pfx", ".p12", ".pyc", ".pyo", ".sqlite", ".sqlite3"}
EGG_INFO_FILES = {
    "PKG-INFO",
    "SOURCES.txt",
    "dependency_links.txt",
    "entry_points.txt",
    "license_files.txt",
    "requires.txt",
    "top_level.txt",
}
DIST_INFO_FILES = {
    "METADATA",
    "WHEEL",
    "RECORD",
    "entry_points.txt",
    "top_level.txt",
}
ASSET_SCOPES = ("catalogs/", "profiles/", ".agents/")


class CheckError(Exception):
    pass


def _fail(message):
    raise CheckError(message)


def _asset_paths():
    module = ast.parse((ROOT / "build_helpers.py").read_text(encoding="utf-8"))
    for node in module.body:
        name = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                value = node.value
        if name == "ASSET_PATHS" and value is not None:
            result = ast.literal_eval(value)
            if isinstance(result, tuple) and all(isinstance(item, str) for item in result):
                return result
    _fail("Cannot read the public asset allowlist")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    try:
        if path.is_symlink() or not path.is_file():
            _fail(f"Missing or linked canonical file: {path.relative_to(ROOT)}")
        return path.read_bytes()
    except OSError as exc:
        _fail(f"Cannot read canonical file {path.relative_to(ROOT)}: {exc}")


def _project_identity():
    try:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        name = data["project"]["name"].replace("-", "_")
        version = data["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        _fail(f"Cannot read project identity: {exc}")
    return name, version


def _module_names():
    names = sorted(path.name for path in (ROOT / "src/agentweaver").glob("*.py"))
    if not names:
        _fail("No package modules found")
    return names


def _canonical_hashes():
    return {relative: _sha256(_read(ROOT / relative)) for relative in _asset_paths()}


def _check_canonical_files(files, prefix, canonical_root, relatives, label):
    for relative in relatives:
        archive_path = prefix + relative
        if archive_path not in files:
            _fail(f"{label} is missing canonical file: {relative}")
        if _sha256(files[archive_path]) != _sha256(_read(canonical_root / relative)):
            _fail(f"{label} canonical file hash mismatch: {relative}")


def _is_control_or_surrogate(character):
    return unicodedata.category(character) in ("Cc", "Cs")


def _check_component(component, name):
    if not component or component in (".", ".."):
        _fail(f"Invalid archive member component: {name}")
    if any(_is_control_or_surrogate(character) for character in component):
        _fail(f"Control character in archive member: {name}")
    if any(character in INVALID_WINDOWS_CHARS for character in component):
        _fail(f"Invalid character in archive member: {name}")
    if component.endswith((".", " ")):
        _fail(f"Trailing dot or space in archive member: {name}")
    stem = component.split(".", 1)[0].rstrip(" ").casefold()
    if stem in RESERVED_WINDOWS_NAMES:
        _fail(f"Reserved Windows name in archive member: {name}")


def _safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        _fail(f"Unsafe archive member: {name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts:
        _fail(f"Absolute archive member: {name}")
    if any(part in ("", ".", "..") for part in pure.parts):
        _fail(f"Traversing archive member: {name}")
    if len(pure.parts[0]) >= 2 and pure.parts[0][1] == ":":
        _fail(f"Drive-qualified archive member: {name}")
    for component in pure.parts:
        _check_component(component, name)
    lowered = pure.as_posix().lower()
    for part in pure.parts:
        if part.lower() in SENSITIVE_PARTS:
            _fail(f"Sensitive archive entry: {name}")
    if pure.name.lower() in SENSITIVE_NAMES or pure.name.lower().startswith(".env."):
        _fail(f"Sensitive archive entry: {name}")
    if any(lowered.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
        _fail(f"Sensitive archive entry: {name}")
    return pure


def _read_tar(path):
    files = {}
    names = set()
    folded = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                pure = _safe_name(member.name)
                normalized = pure.as_posix()
                if normalized in names:
                    _fail(f"Duplicate archive member: {member.name}")
                names.add(normalized)
                key = normalized.casefold()
                if key in folded:
                    _fail(f"Case-colliding archive member: {member.name}")
                folded.add(key)
                if member.isdir():
                    continue
                if member.issym() or member.islnk() or not member.isfile():
                    _fail(f"Unsupported archive member: {member.name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    _fail(f"Cannot read archive member: {member.name}")
                files[normalized] = extracted.read()
    except (OSError, tarfile.TarError) as exc:
        _fail(f"Cannot read source distribution: {exc}")
    return files


def _read_wheel(path):
    files = {}
    names = set()
    folded = set()
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                pure = _safe_name(member.filename)
                normalized = pure.as_posix()
                if normalized in names:
                    _fail(f"Duplicate archive member: {member.filename}")
                names.add(normalized)
                key = normalized.casefold()
                if key in folded:
                    _fail(f"Case-colliding archive member: {member.filename}")
                folded.add(key)
                if member.is_dir():
                    continue
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    _fail(f"Symlinked archive member: {member.filename}")
                files[normalized] = archive.read(member)
    except (OSError, zipfile.BadZipFile) as exc:
        _fail(f"Cannot read wheel: {exc}")
    return files


def _check_assets(files, prefix, expected, label):
    actual = {}
    for name, data in files.items():
        if not name.startswith(prefix):
            continue
        relative = name[len(prefix) :]
        if prefix or relative.startswith(ASSET_SCOPES):
            actual[relative] = data
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        _fail(f"{label} asset allowlist mismatch; missing={missing} extra={extra}")
    for relative, data in actual.items():
        if _sha256(data) != expected[relative]:
            _fail(f"{label} asset hash mismatch: {relative}")


def _check_license(files, label):
    canonical = _read(ROOT / "LICENSE")
    candidates = [
        (name, data)
        for name, data in files.items()
        if PurePosixPath(name).name == "LICENSE"
    ]
    if not candidates:
        _fail(f"{label} does not include LICENSE")
    for name, data in candidates:
        if _sha256(data) != _sha256(canonical):
            _fail(f"{label} LICENSE differs from canonical: {name}")


def _check_sdist(files, name, version, expected, modules):
    top = f"{name}-{version}"
    prefixes = {PurePosixPath(item).parts[0] for item in files}
    if prefixes != {top}:
        _fail(f"Unexpected source distribution top level: {sorted(prefixes)}")
    relative = {
        name[len(top) + 1 :]: data
        for name, data in files.items()
        if name.startswith(top + "/")
    }
    allowed_top = {
        "PKG-INFO",
        "README.md",
        "LICENSE",
        "MANIFEST.in",
        "build_helpers.py",
        "pyproject.toml",
        "setup.cfg",
        "catalogs",
        "profiles",
        ".agents",
        "src",
        f"{name}.egg-info",
    }
    for item in relative:
        first = PurePosixPath(item).parts[0]
        if first not in allowed_top:
            _fail(f"Unexpected source distribution entry: {item}")
    for required in ("PKG-INFO", "LICENSE", "build_helpers.py", "pyproject.toml"):
        if required not in relative:
            _fail(f"Source distribution is missing {required}")
    _check_assets(relative, "", expected, "sdist")
    _check_license(relative, "sdist")
    _check_canonical_files(relative, "", ROOT, ("build_helpers.py", "pyproject.toml"), "sdist")
    _check_canonical_files(relative, "src/agentweaver/", ROOT / "src/agentweaver",
                           [*modules, "py.typed"], "sdist")
    source_modules = {
        item[len("src/agentweaver/") :]
        for item in relative
        if item.startswith("src/agentweaver/") and item.endswith(".py")
    }
    if source_modules != set(modules):
        _fail(f"Source distribution modules differ: {sorted(source_modules)}")
    for item in relative:
        if not item.startswith("src/"):
            continue
        inner = item[len("src/") :]
        parts = PurePosixPath(inner).parts
        if len(parts) == 2 and parts[0] == "agentweaver":
            if parts[1] not in set(modules) | {"py.typed"}:
                _fail(f"Unexpected source package entry: {item}")
        elif parts and parts[0].endswith(".egg-info"):
            if len(parts) != 2 or parts[1] not in EGG_INFO_FILES:
                _fail(f"Unexpected egg-info entry: {item}")
        else:
            _fail(f"Unexpected source entry: {item}")


def _check_wheel(files, name, version, expected, modules):
    dist_info = f"{name}-{version}.dist-info"
    roots = {PurePosixPath(item).parts[0] for item in files}
    if roots != {"agentweaver", dist_info}:
        _fail(f"Unexpected wheel top level: {sorted(roots)}")
    _check_assets(files, "agentweaver/_data/", expected, "wheel")
    _check_license(files, "wheel")
    package_files = {
        item[len("agentweaver/") :]
        for item in files
        if item.startswith("agentweaver/") and "/" not in item[len("agentweaver/") :]
    }
    missing = set(modules) - package_files
    extra = package_files - set(modules) - {"py.typed"}
    if missing or extra:
        _fail(f"Wheel package files differ; missing={sorted(missing)} extra={sorted(extra)}")
    _check_canonical_files(files, "agentweaver/", ROOT / "src/agentweaver", [*modules, "py.typed"], "wheel")
    for item in files:
        if item.startswith("agentweaver/") and not item.startswith("agentweaver/_data/"):
            relative = item[len("agentweaver/") :]
            if relative not in set(modules) | {"py.typed"}:
                _fail(f"Unexpected wheel package entry: {item}")
        if not item.startswith(dist_info + "/"):
            continue
        relative = item[len(dist_info) + 1 :]
        if relative in DIST_INFO_FILES:
            continue
        if relative in ("licenses/LICENSE", "LICENSE"):
            continue
        _fail(f"Unexpected wheel metadata entry: {item}")


def _extract_sdist(path, target):
    files = _read_tar(path)
    for name, data in files.items():
        destination = target.joinpath(*PurePosixPath(name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return files


def _clean_environment():
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}
    }
    environment.update(
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PIP_NO_INDEX="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
    )
    return environment


def _run(command, cwd, environment):
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        output = (result.stdout + "\n" + result.stderr).strip()
        _fail(f"Command failed ({result.returncode}): {' '.join(command)}\n{output[-4000:]}")
    return result.stdout


def _venv_python(directory):
    if os.name == "nt":
        return directory / "Scripts/python.exe"
    return directory / "bin/python"


def _install(wheel, base, label):
    environment = _clean_environment()
    directory = base / label
    venv.EnvBuilder(with_pip=True, clear=True).create(directory)
    python = _venv_python(directory)
    outside = base / f"{label}-cwd"
    outside.mkdir()
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-cache-dir",
            str(wheel),
        ],
        outside,
        environment,
    )
    module_path = _run(
        [str(python), "-c", "import agentweaver; print(agentweaver.__file__)"],
        outside,
        environment,
    ).strip()
    try:
        Path(module_path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        _fail(f"{label} imported from the checkout: {module_path}")
    return python, outside, environment


def _smoke(python, outside, environment, host_os):
    commands = [
        ["validate"],
        ["inventory", "--format", "json"],
        [
            "doctor",
            "--agent",
            "kilo",
            "--surface",
            "cli",
            "--os",
            host_os,
            "--format",
            "json",
        ],
        [
            "render",
            "--agent",
            "kilo",
            "--surface",
            "cli",
            "--profile",
            "efficient",
            "--output-dir",
            str(outside / "render-kilo"),
        ],
        [
            "render",
            "--agent",
            "codex",
            "--surface",
            "cli",
            "--profile",
            "efficient",
            "--output-dir",
            str(outside / "render-codex"),
        ],
        [
            "bundle",
            "--agent",
            "aider",
            "--surface",
            "cli",
            "--profile",
            "efficient",
            "--skill",
            "token-efficient-retrieval",
            "--os",
            host_os,
            "--output-dir",
            str(outside / "bundle-aider"),
        ],
        [
            "provider",
            "--provider",
            "openrouter",
            "--agent",
            "aider",
            "--surface",
            "cli",
            "--model",
            "openai/gpt-5-mini",
            "--output-dir",
            str(outside / "provider-aider"),
        ],
    ]
    for arguments in commands:
        _run([str(python), "-m", "agentweaver", *arguments], outside, environment)


def _host_os():
    mapping = {"win32": "windows", "darwin": "macos", "linux": "linux"}
    return mapping.get(sys.platform, sys.platform)


def _single(directory, pattern, label):
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        _fail(f"Expected one {label} in {directory}, found {[item.name for item in matches]}")
    return matches[0]


def _build_root(dist):
    output = Path(dist).resolve()
    output.mkdir(parents=True, exist_ok=True)
    name, version = _project_identity()
    for pattern in (f"{name}-{version}*.whl", f"{name}-{version}.tar.gz"):
        for path in output.glob(pattern):
            if path.is_file() or path.is_symlink():
                path.unlink()
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(output),
        ],
        ROOT,
        _clean_environment(),
    )
    return (
        _single(output, f"{name}-{version}.tar.gz", "source distribution"),
        _single(output, f"{name}-{version}*.whl", "wheel"),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args(argv)
    try:
        dist = args.dist.resolve()
        if args.build:
            sdist, wheel = _build_root(dist)
        else:
            sdist = args.sdist or _single(dist, "*.tar.gz", "source distribution")
            wheel = args.wheel or _single(dist, "*.whl", "wheel")
        expected = _canonical_hashes()
        name, version = _project_identity()
        modules = _module_names()
        sdist_files = _read_tar(sdist.resolve())
        wheel_files = _read_wheel(wheel.resolve())
        _check_sdist(sdist_files, name, version, expected, modules)
        _check_wheel(wheel_files, name, version, expected, modules)
        print(f"Archive checks passed for {sdist.name} and {wheel.name}")
        with tempfile.TemporaryDirectory(prefix="agentweaver dist ") as directory:
            # Canonicalize this harness-owned allocation, including macOS /var temp aliases.
            # The staged writer must still reject links in user-selected output paths.
            base = Path(directory).resolve()
            extracted = base / "sdist"
            _extract_sdist(sdist.resolve(), extracted)
            source = extracted / f"{name}-{version}"
            wheel_dir = base / "sdist-wheel"
            _run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--no-isolation",
                    "--wheel",
                    "--outdir",
                    str(wheel_dir),
                    str(source),
                ],
                source,
                _clean_environment(),
            )
            rebuilt = _single(wheel_dir, "*.whl", "sdist-built wheel")
            rebuilt_files = _read_wheel(rebuilt)
            _check_wheel(rebuilt_files, name, version, expected, modules)
            host_os = _host_os()
            python, outside, environment = _install(wheel.resolve(), base, "original")
            _smoke(python, outside, environment, host_os)
            python, outside, environment = _install(rebuilt, base, "rebuilt")
            _smoke(python, outside, environment, host_os)
        print("Distribution checks passed")
        return 0
    except CheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
