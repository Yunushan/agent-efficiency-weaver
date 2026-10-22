"""Safe, reviewable MCP settings renderer."""
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


class WeaverError(ValueError):
    pass


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
        if not isinstance(item, dict) or item.get("kind") != "remote":
            errors.append("Only remote HTTPS MCP servers supported")
            continue
        if set(item) - {"kind", "url", "description", "default_enabled"}:
            errors.append("Unsupported MCP fields; credentials and permissions are not accepted")
        if not safe_https_url(item.get("url")):
            errors.append("MCP requires credential-free HTTPS URL without query or fragment")
        if item.get("default_enabled") is not False:
            errors.append("MCP server must default to disabled")
        if "description" in item and not safe_text(item["description"]):
            errors.append("Invalid MCP description")
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


def select_servers(profile: dict[str, Any], catalog: dict[str, Any], enabled: list[str]) -> dict[str, str]:
    errors = validate_catalog(catalog) + validate_profile(profile, catalog)
    if errors:
        raise WeaverError("; ".join(errors))
    if not isinstance(enabled, list) or any(not safe_name(name) for name in enabled):
        raise WeaverError("MCP selection must be a list of server names")
    if len(enabled) != len(set(enabled)) or len(enabled) > profile["max_mcp_servers"]:
        raise WeaverError("Duplicate or excessive MCP selection")
    if any(name not in profile["allowed_mcp"] for name in enabled):
        raise WeaverError("MCP not permitted by profile")
    return {name: catalog["servers"][name]["url"] for name in sorted(enabled)}


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
