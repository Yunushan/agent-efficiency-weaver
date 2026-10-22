"""Bundled public resource selection and materialization."""
import json
import re
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath

from .config import WeaverError

ASSET_PATHS: tuple[str, ...] = (
    ".agents/skills/benchmark-design/SKILL.md",
    ".agents/skills/gitlab-ci-review/SKILL.md",
    ".agents/skills/linux-incident-triage/SKILL.md",
    ".agents/skills/rke2-troubleshooting/SKILL.md",
    ".agents/skills/security-review/SKILL.md",
    ".agents/skills/token-efficient-retrieval/SKILL.md",
    "catalogs/integrations.json",
    "catalogs/mcp-servers.json",
    "catalogs/operating-systems.json",
    "profiles/balanced.json",
    "profiles/efficient.json",
    "profiles/enterprise-secure.json",
)

_FRONT_MATTER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def asset_paths() -> tuple[str, ...]:
    return ASSET_PATHS


def _normalize(raw: object) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise WeaverError("Resource path is not allowed")
    if "\\" in raw or "\x00" in raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise WeaverError("Resource path is not allowed")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts:
        raise WeaverError("Resource path is not allowed")
    if any(part in ("", ".", "..") for part in path.parts):
        raise WeaverError("Resource path is not allowed")
    if path.as_posix() != raw:
        raise WeaverError("Resource path is not allowed")
    if len(path.parts[0]) >= 2 and path.parts[0][1] == ":":
        raise WeaverError("Resource path is not allowed")
    return raw


def select_assets(paths: Iterable[str] | None = None) -> tuple[str, ...]:
    if paths is None:
        return ASSET_PATHS
    if isinstance(paths, (str, bytes)):
        raise WeaverError("Resource paths must be a sequence")
    try:
        requested = list(paths)
    except TypeError:
        raise WeaverError("Resource paths must be a sequence") from None
    if not requested:
        return ()
    chosen: set[str] = set()
    seen: set[str] = set()
    for raw in requested:
        relative = _normalize(raw)
        if relative in seen:
            raise WeaverError("Resource path is duplicated")
        seen.add(relative)
        if relative in ASSET_PATHS:
            matches: tuple[str, ...] = (relative,)
        else:
            prefix = relative + "/"
            matches = tuple(item for item in ASSET_PATHS if item.startswith(prefix))
            if not matches:
                raise WeaverError("Resource path is not allowed")
        overlap = chosen.intersection(matches)
        if overlap:
            raise WeaverError("Resource path is duplicated")
        chosen.update(matches)
    return tuple(item for item in ASSET_PATHS if item in chosen)


def _duplicate_key(_pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in _pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> object:
    raise ValueError("constant")


def _validate_skill(relative: str, text: str) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0] != "---":
        raise WeaverError("Bundled resource is malformed")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise WeaverError("Bundled resource is malformed") from None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            raise WeaverError("Bundled resource is malformed")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    name = fields.get("name", "").strip("\"'")
    description = fields.get("description", "").strip("\"'")
    expected = PurePosixPath(relative).parent.name
    if not _FRONT_MATTER_NAME.fullmatch(name) or name != expected or not description:
        raise WeaverError("Bundled resource is malformed")


def _validate_asset(relative: str, data: bytes) -> None:
    if not data:
        raise WeaverError("Bundled resource is malformed")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise WeaverError("Bundled resource is malformed") from None
    if relative.endswith(".json"):
        try:
            value = json.loads(
                text,
                object_pairs_hook=_duplicate_key,
                parse_constant=_invalid_constant,
            )
        except (ValueError, RecursionError):
            raise WeaverError("Bundled resource is malformed") from None
        if not isinstance(value, dict):
            raise WeaverError("Bundled resource is malformed")
    elif relative.endswith("/SKILL.md"):
        _validate_skill(relative, text)


def _bundled_bytes(anchor: Traversable, relative: str) -> bytes:
    try:
        node = anchor
        for part in PurePosixPath(relative).parts:
            node = node.joinpath(part)
        if not node.is_file():
            raise WeaverError("Bundled resource is missing")
        data = node.read_bytes()
    except WeaverError:
        raise
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        raise WeaverError("Bundled resources are unavailable") from None
    _validate_asset(relative, data)
    return data


def _check_override(root: Path, selected: tuple[str, ...]) -> None:
    for relative in selected:
        try:
            present = root.joinpath(*PurePosixPath(relative).parts).is_file()
        except OSError:
            raise WeaverError("Resource root is unavailable") from None
        if not present:
            raise WeaverError("Required resource is missing")


@contextmanager
def resource_root(
    override: str | Path | None = None,
    *,
    paths: Iterable[str] | None = None,
) -> Iterator[Path]:
    selected = select_assets(paths)
    if override is not None:
        root = Path(override)
        try:
            available = root.is_dir()
        except OSError:
            available = False
        if not available:
            raise WeaverError("Resource root is unavailable")
        _check_override(root, selected)
        yield root
        return
    try:
        anchor = resources.files("agentweaver").joinpath("_data")
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        raise WeaverError("Bundled resources are unavailable") from None
    with tempfile.TemporaryDirectory(prefix="agentweaver-resources-") as directory:
        root = Path(directory)
        for relative in selected:
            data = _bundled_bytes(anchor, relative)
            destination = root.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        yield root
