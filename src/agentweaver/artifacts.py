import hashlib
import json
import os
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, NoReturn

from .config import WeaverError

MANIFEST_NAME = ".agentweaver-manifest.json"
_SCHEMA_VERSION = 1
_GENERATOR = "agentweaver"
_INVALID_WINDOWS_CHARS = frozenset('<>:"|?*')
_RESERVED_WINDOWS_NAMES = frozenset(
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


@dataclass(frozen=True)
class RenderedArtifact:
    relative_path: PurePosixPath
    content: bytes
    kind: str


def _fail(message: str) -> NoReturn:
    raise WeaverError(message)


def _is_control_or_surrogate(character: str) -> bool:
    return unicodedata.category(character) in ("Cc", "Cs")


def _safe_label(value: object) -> str:
    text = str(value)
    return "".join("?" if _is_control_or_surrogate(character) else character for character in text)


def _validate_component(component: str) -> None:
    label = _safe_label(component)
    if not component or component in (".", ".."):
        _fail(f"Invalid artifact path component: {label}")
    if any(_is_control_or_surrogate(character) for character in component):
        _fail(f"Control character in artifact path component: {label}")
    if any(character in _INVALID_WINDOWS_CHARS for character in component):
        _fail(f"Invalid character in artifact path component: {label}")
    if component.endswith((".", " ")):
        _fail(f"Artifact path component has a trailing dot or space: {label}")
    stem = component.split(".", 1)[0].rstrip(" ").casefold()
    if stem in _RESERVED_WINDOWS_NAMES:
        _fail(f"Reserved Windows name in artifact path: {label}")
    if component.casefold() == MANIFEST_NAME.casefold():
        _fail("Manifest path is reserved")


def _validate_path(path: PurePosixPath, label: str) -> str:
    if not isinstance(path, PurePosixPath):
        _fail(f"{label} must be a PurePosixPath")
    raw = path.as_posix()
    if not raw or raw == ".":
        _fail(f"Empty {label}")
    if path.is_absolute():
        _fail(f"Absolute {label} is not allowed")
    if "\\" in raw:
        _fail(f"Backslash in {label} is not allowed")
    parts = path.parts
    if not parts:
        _fail(f"Empty {label}")
    for component in parts:
        _validate_component(component)
    return raw


def _validate_kind(kind: object, label: str = "artifact kind") -> None:
    if not isinstance(kind, str) or not kind:
        _fail(f"{label} must be a non-empty string")
    if any(_is_control_or_surrogate(character) for character in kind):
        _fail(f"{label} contains control or invalid Unicode characters")


def _validate_artifact(item: object) -> str:
    if not isinstance(item, RenderedArtifact):
        _fail("Artifact must be RenderedArtifact")
    path = _validate_path(item.relative_path, "artifact path")
    if not isinstance(item.content, bytes):
        _fail("Artifact content must be bytes")
    try:
        text = item.content.decode("utf-8")
    except UnicodeDecodeError:
        _fail(f"Artifact content is not valid UTF-8: {_safe_label(path)}")
    if "\r" in text:
        _fail(f"Artifact content contains CR: {_safe_label(path)}")
    _validate_kind(item.kind)
    return path


def _validate_no_conflicts(paths: Iterable[str], label: str) -> None:
    seen = {}
    for path in paths:
        key = path.casefold()
        if key in seen:
            _fail(f"Duplicate {label} path")
        seen[key] = path
    keys = set(seen)
    for key in keys:
        parts = key.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in keys:
                _fail(f"Conflicting {label} paths")


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise WeaverError("Unable to inspect destination path") from exc


def _is_link(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    attributes = getattr(st, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_read_only(st: os.stat_result) -> bool:
    attributes = getattr(st, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_READONLY", 0))


def _is_writable(path: Path, *, directory: bool = False) -> bool:
    try:
        mode = os.W_OK | os.X_OK if directory else os.W_OK
        return os.access(path, mode)
    except (OSError, ValueError):
        return False


def _normalise_root(root: Path) -> Path:
    try:
        requested = Path(root)
    except (TypeError, ValueError) as exc:
        raise WeaverError("Invalid output root") from exc
    if any(part == ".." for part in requested.parts):
        _fail("Output root must not contain '..'")
    if requested.drive and not requested.is_absolute():
        _fail("Output root must not be drive-relative")
    if requested.is_absolute():
        return requested
    try:
        base = Path.cwd()
    except (OSError, ValueError) as exc:
        raise WeaverError("Unable to determine current directory") from exc
    return base / requested


def _root_chain(root: Path) -> list[Path]:
    chain: list[Path] = []
    current = root
    while True:
        chain.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    chain.reverse()
    return chain


def _inspect_root(root: Path) -> list[Path]:
    missing: list[Path] = []
    existing: list[Path] = []
    missing_seen = False
    for path in _root_chain(root):
        st = _lstat(path)
        if st is None:
            missing_seen = True
            missing.append(path)
            continue
        if missing_seen:
            _fail("Output root has an existing path below a missing ancestor")
        if _is_link(st):
            _fail("Output root ancestor is a link")
        if not stat.S_ISDIR(st.st_mode):
            _fail("Output root ancestor is not a directory")
        existing.append(path)
    if not existing:
        _fail("Unable to locate output root anchor")
    nearest = existing[-1]
    if not _is_writable(nearest, directory=True):
        _fail("Output root ancestor is not writable")
    return missing


def _create_root_chain(missing: list[Path]) -> None:
    for path in missing:
        try:
            path.mkdir()
        except FileExistsError:
            st = _lstat(path)
            if st is None or _is_link(st) or not stat.S_ISDIR(st.st_mode):
                _fail("Unable to create output root safely")
        except (OSError, ValueError) as exc:
            raise WeaverError("Unable to create output root") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _load_manifest(
    root: Path,
) -> tuple[list[dict[str, str]], Path, os.stat_result | None]:
    path = root / MANIFEST_NAME
    st = _lstat(path)
    if st is None:
        return [], path, None
    if _is_link(st) or not stat.S_ISREG(st.st_mode):
        _fail("Manifest must be a regular file, not a link or directory")
    try:
        raw = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise WeaverError("Unable to read manifest") from exc
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise WeaverError("Invalid manifest JSON") from exc
    if not isinstance(data, dict):
        _fail("Invalid manifest")
    if set(data) != {"schema_version", "generator", "entries"}:
        _fail("Invalid manifest fields")
    if type(data["schema_version"]) is not int or data["schema_version"] != _SCHEMA_VERSION:
        _fail("Unsupported manifest schema version")
    if data["generator"] != _GENERATOR:
        _fail("Invalid manifest generator")
    if not isinstance(data["entries"], list):
        _fail("Invalid manifest entries")

    entries: list[dict[str, str]] = []
    paths: list[str] = []
    for entry in data["entries"]:
        if not isinstance(entry, dict):
            _fail("Invalid manifest entry")
        if set(entry) - {"path", "sha256", "kind"} or not {"path", "sha256"} <= set(entry):
            _fail("Invalid manifest entry fields")
        path_value = entry["path"]
        if not isinstance(path_value, str):
            _fail("Invalid manifest entry path")
        pure_path = PurePosixPath(path_value)
        canonical = _validate_path(pure_path, "manifest entry path")
        if canonical != path_value:
            _fail("Non-canonical manifest entry path")
        digest = entry["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            _fail("Invalid manifest entry hash")
        loaded = {"path": path_value, "sha256": digest}
        if "kind" in entry:
            _validate_kind(entry["kind"], "manifest entry kind")
            loaded["kind"] = entry["kind"]
        entries.append(loaded)
        paths.append(path_value)
    _validate_no_conflicts(paths, "manifest entry")
    return entries, path, st


def _preflight_destination(
    root: Path,
    parts: tuple[str, ...],
    force: bool,
    owned: dict[str, dict[str, str]],
) -> tuple[Path, bool]:
    current = root
    for component in parts[:-1]:
        current = current / component
        st = _lstat(current)
        if st is None:
            break
        if _is_link(st):
            _fail("Refusing to write through a link")
        if not stat.S_ISDIR(st.st_mode):
            _fail("Artifact parent is not a directory")
        if not _is_writable(current, directory=True):
            _fail("Artifact parent is not writable")

    relative = "/".join(parts)
    destination = root.joinpath(*parts)
    st = _lstat(destination)
    if st is None:
        return destination, False
    if _is_link(st):
        _fail("Refusing to write through a link")
    if not stat.S_ISREG(st.st_mode):
        _fail("Artifact destination is not a regular file")
    if not force:
        _fail("Refusing to overwrite an existing artifact")
    entry = owned.get(relative)
    if entry is None:
        _fail("Refusing to overwrite an unmanaged file")
    if _is_read_only(st) or not _is_writable(destination):
        _fail("Artifact destination is not writable")
    try:
        current_bytes = destination.read_bytes()
    except (OSError, ValueError) as exc:
        raise WeaverError("Unable to read existing artifact") from exc
    if hashlib.sha256(current_bytes).hexdigest() != entry["sha256"]:
        _fail("Refusing to overwrite a manually edited file")
    return destination, True


def _ensure_parent(root: Path, parts: tuple[str, ...]) -> None:
    current = root
    for component in parts[:-1]:
        current = current / component
        st = _lstat(current)
        if st is None:
            try:
                current.mkdir()
            except (OSError, ValueError) as exc:
                raise WeaverError("Unable to create artifact directory") from exc
            continue
        if _is_link(st):
            _fail("Refusing to write through a link")
        if not stat.S_ISDIR(st.st_mode):
            _fail("Artifact parent is not a directory")


def _atomic_write(destination: Path, content: bytes, replace: bool) -> None:
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".agentweaver.tmp",
            dir=str(destination.parent),
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if not replace and _lstat(destination) is not None:
            _fail("Artifact destination appeared during write")
        os.replace(temporary, destination)
        temporary = None
    except WeaverError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise WeaverError("Unable to write artifact") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def validate_artifacts(artifacts: list[RenderedArtifact]) -> list[str]:
    try:
        relatives = [_validate_artifact(item) for item in artifacts]
    except TypeError as exc:
        raise WeaverError("Artifacts must be iterable") from exc
    _validate_no_conflicts(relatives, "artifact")
    return relatives


def write_artifacts(
    root: Path, artifacts: list[RenderedArtifact], *, force: bool = False
) -> list[Path]:
    if type(force) is not bool:
        _fail("force must be a boolean")
    try:
        items = list(artifacts)
    except TypeError as exc:
        raise WeaverError("Artifacts must be iterable") from exc
    if not items:
        return []
    root = _normalise_root(root)

    relatives = validate_artifacts(items)
    missing_root = _inspect_root(root)

    previous_entries, manifest_path, manifest_state = _load_manifest(root)
    if manifest_state is not None:
        if _is_read_only(manifest_state) or not _is_writable(manifest_path):
            _fail("Manifest is not writable")

    owned = {entry["path"]: entry for entry in previous_entries}
    previous_case = {path.casefold(): path for path in owned}
    preflight: list[tuple[tuple[str, ...], Path, bool]] = []
    for relative in relatives:
        if relative.casefold() in previous_case and previous_case[relative.casefold()] != relative:
            _fail("Artifact path case conflicts with existing ownership")
        parts = PurePosixPath(relative).parts
        destination, replace = _preflight_destination(root, parts, force, owned)
        preflight.append((parts, destination, replace))

    merged = {entry["path"].casefold(): entry for entry in previous_entries}
    for item, relative in zip(items, relatives):
        merged[relative.casefold()] = {
            "path": relative,
            "sha256": hashlib.sha256(item.content).hexdigest(),
            "kind": item.kind,
        }
    merged_entries = sorted(merged.values(), key=lambda entry: entry["path"])
    _validate_no_conflicts(
        [entry["path"] for entry in merged_entries], "manifest entry"
    )

    _create_root_chain(missing_root)
    results: list[Path] = []
    for item, (parts, destination, replace) in zip(items, preflight):
        _ensure_parent(root, parts)
        _atomic_write(destination, item.content, replace)
        results.append(destination)

    manifest_data = {
        "schema_version": _SCHEMA_VERSION,
        "generator": _GENERATOR,
        "entries": merged_entries,
    }
    manifest_bytes = (
        json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    _atomic_write(manifest_path, manifest_bytes, manifest_state is not None)
    return results
