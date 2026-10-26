#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ARTIFACT_PATTERNS = (
    "pyproject.toml",
    "build_helpers.py",
    "MANIFEST.in",
    ".gitattributes",
    ".github/workflows/ci.yml",
    ".gitlab-ci.yml",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "docs/*.md",
    "catalogs/*.json",
    "profiles/*.json",
    "src/agentweaver/*.py",
    "src/agentweaver/py.typed",
    "tests/*.py",
    "tests/fixtures/adapters/golden.json",
    "benchmarks/fixtures/synthetic_runs.jsonl",
    ".agents/skills/*/SKILL.md",
    "scripts/check-distribution.py",
    "scripts/check-readiness.py",
    "scripts/install.ps1",
    "scripts/install.sh",
    "scripts/verify-platform.py",
)
MAX_CAPTURE = 20000


def artifact_files(root):
    paths = []
    for pattern in ARTIFACT_PATTERNS:
        for path in root.glob(pattern):
            if path.is_file() and not path.is_symlink():
                paths.append(path)
    return sorted(set(paths), key=lambda item: item.relative_to(root).as_posix())


def hash_artifacts(root):
    result = []
    for path in artifact_files(root):
        data = path.read_bytes()
        result.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return result


def package_version(root):
    # The checks below run the selected source tree. Installed distribution
    # metadata may describe a different checkout or release.
    try:
        import tomllib

        with (root / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)
        value = data.get("project", {}).get("version")
        if isinstance(value, str) and value.strip():
            return value, "pyproject"
    except Exception:
        pass
    return None, "unavailable"


def truncated(value):
    if len(value) <= MAX_CAPTURE:
        return value
    return value[:MAX_CAPTURE] + "\n[output truncated]"


def run_check(root, name, command, timeout):
    environment = os.environ.copy()
    source = str(root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source + (os.pathsep + existing if existing else "")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    environment["NO_COLOR"] = "1"
    started = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        stdout = truncated(completed.stdout)
        stderr = truncated(completed.stderr)
        exit_code = completed.returncode
        status = "passed" if exit_code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = truncated(exc.stdout or "" if isinstance(exc.stdout, str) else "")
        stderr = truncated(f"Timed out after {timeout} seconds")
        exit_code = None
        status = "timeout"
    except OSError as exc:
        stdout = ""
        stderr = truncated(f"Cannot run check: {exc.__class__.__name__}")
        exit_code = None
        status = "error"
    finished = datetime.now(timezone.utc)
    return {
        "name": name,
        "command": list(command),
        "status": status,
        "exit_code": exit_code,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "stdout": stdout,
        "stderr": stderr,
    }


def report_path(path):
    requested = Path(path)
    if ".." in requested.parts:
        raise ValueError("Report output must not contain '..'")
    if requested.drive and not requested.is_absolute():
        raise ValueError("Report output must not be drive-relative")
    absolute = requested if requested.is_absolute() else Path.cwd() / requested
    # Inspect ancestors before descendants, without resolving through a link.
    for component in (*reversed(absolute.parents), absolute):
        try:
            info = os.lstat(component)
        except FileNotFoundError:
            continue
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("Report output must not contain links or reparse points")
        if component == absolute:
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Report output must be a regular file")
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("Report output ancestor must be a directory")
    return absolute


def write_report(path, report, force):
    path = report_path(path)
    if path.exists() and not force:
        raise FileExistsError("Output exists; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    report_path(path)
    body = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if force:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(body)
            report_path(path)
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    else:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    return path


def build_report(root, timeout):
    from agentweaver.platforms import collect_host_facts, detect_host_os

    facts = collect_host_facts()
    version, version_source = package_version(root)
    checks = [
        run_check(
            root,
            "cli-validate",
            [sys.executable, "-m", "agentweaver", "validate", "--root", str(root)],
            timeout,
        ),
        run_check(root, "unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], timeout),
    ]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "detected_os": detect_host_os(facts),
            "system": facts.get("system"),
            "release": facts.get("release"),
            "version": facts.get("version"),
            "machine": facts.get("machine"),
            "architecture": facts.get("machine"),
            "os_release_id": facts.get("os_release_id"),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "architecture": platform.machine(),
        },
        "package": {
            "name": "agent-efficiency-weaver",
            "version": version,
            "version_source": version_source,
        },
        "artifacts": hash_artifacts(root),
        "checks": checks,
        "native_agent_verification": {
            "status": "pending",
            "reason": "Native vendor installation and configuration-consumption checks require a separate manual run.",
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run offline local checks and record evidence.")
    parser.add_argument("--output", type=Path, required=True, help="Explicit JSON evidence output path")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        report = build_report(root, args.timeout)
        path = write_report(args.output, report, args.force)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    failures = [check for check in report["checks"] if check["status"] != "passed"]
    print(f"Evidence written to {path}")
    print(f"Checks: {len(report['checks']) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
