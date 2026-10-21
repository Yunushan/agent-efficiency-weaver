"""Safe, reviewable MCP settings renderer."""
import json
from pathlib import Path
from urllib.parse import urlparse

class WeaverError(ValueError):
    pass

def load_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeaverError(str(exc)) from exc
    if not isinstance(result, dict):
        raise WeaverError("Expected JSON object")
    return result

def validate_catalog(catalog: dict) -> list[str]:
    errors = []
    if not isinstance(catalog.get("servers"), dict):
        return ["Catalog must define servers object"]
    for name, item in catalog["servers"].items():
        if not isinstance(item, dict) or item.get("kind") != "remote":
            errors.append(f"{name}: only remote servers supported")
            continue
        address = item.get("url")
        try:
            parsed = urlparse(address)
            valid = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
        except (ValueError, TypeError):
            valid = False
        if not valid:
            errors.append(f"{name}: HTTPS URL required")
        if item.get("default_enabled") is not False:
            errors.append(f"{name}: server must default to disabled")
    return errors

def validate_profile(profile: dict, catalog: dict) -> list[str]:
    errors = []
    if not isinstance(profile.get("id"), str) or not profile["id"]:
        errors.append("Missing profile id")
    allowed = profile.get("allowed_mcp")
    limit = profile.get("max_mcp_servers")
    if not isinstance(allowed, list) or any(s not in catalog.get("servers", {}) for s in allowed):
        errors.append("Invalid allowed_mcp")
    if type(limit) is not int or limit < 0:
        errors.append("Invalid max_mcp_servers")
    if profile.get("policy", {}).get("allow_production_writes") is not False:
        errors.append("Production writes must be disabled")
    return errors

def render(platform: str, profile: dict, catalog: dict, enabled: list[str]) -> dict:
    if platform not in ("kilo", "abacus"):
        raise WeaverError("Unknown platform")
    errors = validate_catalog(catalog) + validate_profile(profile, catalog)
    if errors:
        raise WeaverError("; ".join(errors))
    if len(enabled) != len(set(enabled)) or len(enabled) > profile["max_mcp_servers"]:
        raise WeaverError("Duplicate or excessive MCP selection")
    if any(name not in profile["allowed_mcp"] for name in enabled):
        raise WeaverError("MCP not permitted by profile")
    if platform == "kilo":
        return {"mcp": {name: {"type": "remote", "url": catalog["servers"][name]["url"], "enabled": True} for name in enabled}}
    return {"mcpServers": {name: {"url": catalog["servers"][name]["url"]} for name in enabled}}

def write_generated(root: Path, platform: str, data: dict, *, force=False) -> Path:
    rel = Path("kilo/.kilo/kilo.json") if platform == "kilo" else Path("abacus/.abacusai/mcp.json")
    out = root / rel
    if out.exists() and not force:
        raise WeaverError(f"Refusing to overwrite {out}; use --force")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out
