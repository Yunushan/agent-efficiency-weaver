"""Safe, reviewable MCP settings renderer."""
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import unquote, urlparse


class WeaverError(ValueError):
    pass


class StdioServer(TypedDict):
    kind: Literal['stdio']
    command: str
    args: list[str]


MCPSelection = str | StdioServer
_STDIO_SECRET = re.compile(
    r'(?:sk-(?:or-|ant-|proj-)?[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{15,}|'
    r'gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16}|(?:Bearer|Basic)\s+\S+|'
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----|[A-Za-z][A-Za-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@|'
    r'(?:password|passwd|api[-_]?key|access[-_]?token|client[-_]?secret|token|secret|authorization)\s*[:=]\s*\S+|'
    r'^--?(?:api[-_]?key|access[-_]?token|token|password|secret|authorization)(?:=|$))',
    re.IGNORECASE,
)
_STDIO_EXPANSION = re.compile(r'\$(?:\{|\(|[A-Za-z_])|%[A-Za-z_][A-Za-z0-9_]*%|\{env:')


def _stdio_text(value: object, *, maximum: int = 4096, empty: bool = True) -> bool:
    if not safe_text(value) or not isinstance(value, str) or (not empty and not value.strip()):
        return False
    if (any(unicodedata.category(char) == 'Cf' for char in value)
            or _STDIO_SECRET.search(value) or _STDIO_EXPANSION.search(value)):
        return False
    try:
        return len(value.encode('utf-8')) <= maximum
    except UnicodeError:
        return False


def _stdio_command(value: object) -> bool:
    if not _stdio_text(value, maximum=1024, empty=False) or not isinstance(value, str):
        return False
    # One executable, with its arguments kept in the separate argv array.
    if any(char in value for char in '|;&<>`\"\'!$%') or value != value.strip():
        return False
    if not any(char.isspace() for char in value):
        return True
    return value.startswith(('/', './', '../', '.\\', '..\\', '\\\\')) or bool(re.match(r'^[A-Za-z]:[\\/]', value))


def safe_name(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", value) is not None


def safe_text(value: object) -> bool:
    return isinstance(value, str) and all(
        ord(char) >= 32 and not 127 <= ord(char) <= 159 and not 0xD800 <= ord(char) <= 0xDFFF
        for char in value
    )


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WeaverError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise WeaverError("Non-finite JSON value")


def _json_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise WeaverError("Non-finite JSON value")
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object,
                            parse_constant=_invalid_constant, parse_float=_json_float)
    except WeaverError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise WeaverError("Cannot read valid UTF-8 JSON object") from exc
    if not isinstance(result, dict):
        raise WeaverError("Expected JSON object")
    return result


def safe_https_url(address: object, *, allow_fragment: bool = False) -> bool:
    if not isinstance(address, str) or not safe_text(address):
        return False
    try:
        parsed = urlparse(address)
        valid = (parsed.scheme == "https" and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and "?" not in address and "\\" not in address
                 and "%" not in parsed.netloc
                 and not any(char.isspace() for char in address)
                 and safe_text(unquote(address)))
        if allow_fragment:
            valid = valid and not any(char in unquote(parsed.fragment) for char in "=&?")
        else:
            valid = valid and not parsed.fragment and "#" not in address
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            valid = False
        return valid
    except (ValueError, TypeError, UnicodeError):
        return False


def validate_catalog(catalog: Any) -> list[str]:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("servers"), dict):
        return ["Catalog must define servers object"]
    errors = []
    if set(catalog) - {"servers", "schema_version"}:
        errors.append("Unsupported catalog fields")
    if "schema_version" in catalog and (type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1):
        errors.append("Unsupported catalog schema")
    for name, item in catalog["servers"].items():
        if not safe_name(name):
            errors.append("Invalid MCP server name")
        if not isinstance(item, dict) or item.get("kind") not in ("remote", "stdio"):
            errors.append("MCP server kind must be remote or stdio")
            continue
        fields = ({"kind", "url", "description", "default_enabled"} if item["kind"] == "remote"
                  else {"kind", "command", "args", "description", "default_enabled"})
        if set(item) - fields:
            errors.append("Unsupported MCP fields; credentials and permissions are not accepted")
        if item["kind"] == "remote":
            if not safe_https_url(item.get("url")):
                errors.append("MCP requires credential-free HTTPS URL without query or fragment")
        else:
            if not _stdio_command(item.get("command")):
                errors.append("Stdio MCP command must be one bounded credential-free executable without expansion")
            args = item.get("args")
            if (not isinstance(args, list) or len(args) > 128
                    or any(not _stdio_text(arg) for arg in args)):
                errors.append("Stdio MCP args must be a bounded array of credential-free UTF-8 strings without expansion")
            elif sum(len(arg.encode('utf-8')) for arg in args) > 32768:
                errors.append("Stdio MCP argument bytes exceed the 32 KiB limit")
        if item.get("default_enabled") is not False:
            errors.append("MCP server must default to disabled")
        if "description" in item and not safe_text(item["description"]):
            errors.append("Invalid MCP description")
        elif item["kind"] == "stdio" and "description" in item and not _stdio_text(item["description"]):
            errors.append("Stdio MCP description must be bounded and credential-free")
    return errors


def validate_profile(profile: Any, catalog: Any) -> list[str]:
    if not isinstance(profile, dict):
        return ["Profile must be an object"]
    errors = []
    if set(profile) - {"id", "description", "max_mcp_servers", "allowed_mcp", "retrieval", "policy"}:
        errors.append("Unsupported profile fields")
    if not safe_name(profile.get("id")):
        errors.append("Invalid profile id")
    if "description" in profile and not safe_text(profile["description"]):
        errors.append("Invalid profile description")
    servers = catalog.get("servers", {}) if isinstance(catalog, dict) else {}
    if not isinstance(servers, dict):
        servers = {}
    allowed = profile.get("allowed_mcp")
    if (not isinstance(allowed, list) or any(not safe_name(s) for s in allowed)
            or len(allowed) != len(set(allowed)) or any(s not in servers for s in allowed)):
        errors.append("Invalid allowed_mcp")
    limit = profile.get("max_mcp_servers")
    if type(limit) is not int or limit < 0:
        errors.append("Invalid max_mcp_servers")
    if profile.get("id") == "enterprise-secure" and (limit != 0 or allowed != []):
        errors.append("enterprise-secure must retain zero generated MCP selections")
    policy = profile.get("policy")
    if (not isinstance(policy, dict) or set(policy) != {"allow_production_writes"}
            or policy.get("allow_production_writes") is not False):
        errors.append("Production writes must be disabled; unsupported policy permissions")
    retrieval = profile.get("retrieval")
    if (not isinstance(retrieval, dict)
            or set(retrieval) != {"max_snippets", "avoid_full_repo_dumps"}
            or type(retrieval.get("max_snippets")) is not int or retrieval["max_snippets"] <= 0
            or type(retrieval.get("avoid_full_repo_dumps")) is not bool):
        errors.append("Invalid retrieval policy")
    return errors


def select_servers(profile: dict[str, Any], catalog: dict[str, Any], enabled: list[str]) -> dict[str, MCPSelection]:
    errors = validate_catalog(catalog) + validate_profile(profile, catalog)
    if errors:
        raise WeaverError("; ".join(errors))
    if not isinstance(enabled, list) or any(not safe_name(name) for name in enabled):
        raise WeaverError("MCP selection must be a list of server names")
    if len(enabled) != len(set(enabled)) or len(enabled) > profile["max_mcp_servers"]:
        raise WeaverError("Duplicate or excessive MCP selection")
    if any(name not in profile["allowed_mcp"] for name in enabled):
        raise WeaverError("MCP not permitted by profile")
    selected: dict[str, MCPSelection] = {}
    for name in sorted(enabled):
        item = catalog["servers"][name]
        selected[name] = (item["url"] if item["kind"] == "remote" else
                          StdioServer(kind="stdio", command=item["command"], args=list(item["args"])))
    return selected


def render(platform: str, profile: dict[str, Any], catalog: dict[str, Any], enabled: list[str]) -> dict[str, Any]:
    if platform not in ("kilo", "abacus"):
        raise WeaverError("Unknown platform")
    from .adapters import mcp_document

    return mcp_document(platform, select_servers(profile, catalog, enabled))


def write_generated(root: Path, platform: str, data: dict[str, Any], *, force: bool = False) -> Path:
    from pathlib import PurePosixPath

    from .artifacts import RenderedArtifact, write_artifacts

    if platform not in ("kilo", "abacus"):
        raise WeaverError("Unknown platform")
    path = "kilo/.kilo/kilo.json" if platform == "kilo" else "abacus/.abacusai/mcp.json"
    try:
        content = (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise WeaverError("Cannot serialize artifact") from exc
    return write_artifacts(root, [RenderedArtifact(PurePosixPath(path), content, "mcp")], force=force)[0]
