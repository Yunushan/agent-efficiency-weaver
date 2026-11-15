"""Offline model catalogs, caller-supplied snapshots and staged model configuration.

A valid identifier, a catalog listing, and successful inference are separate facts.
This module never discovers credentials, contacts a provider, or runs inference.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from urllib.parse import parse_qsl, urlsplit

from .artifacts import RenderedArtifact
from .config import WeaverError, _invalid_constant, _json_float, _json_object, load_json, safe_https_url
from .integrations import IntegrationSpec, get_integration, load_integrations
from .providers import AIR_IDE_AUTHORIZATION, AIR_IDE_SESSION_SETTINGS

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MODEL = re.compile(r"^~?[A-Za-z0-9][A-Za-z0-9._:+@/-]*$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_LM_STUDIO_REASONING_OPTIONS = {"off", "on", "low", "medium", "high"}
_LM_STUDIO_NONCHAT = {
    "openai-embeddings": ("embedding", "/embeddings"),
    "openai-decisions": ("decision", "/decisions"),
    "jev-systemone": ("decision", "/systemone"),
}
_OPENROUTER_TASK_ENDPOINTS = {
    "openrouter-completions": "/api/v1/completions",
    "openrouter-messages": "/api/v1/messages",
    "openrouter-embeddings": "/api/v1/embeddings",
    "openrouter-rerank": "/api/v1/rerank",
    "openrouter-images": "/api/v1/images",
    "openrouter-videos": "/api/v1/videos",
    "openrouter-speech": "/api/v1/audio/speech",
    "openrouter-transcriptions": "/api/v1/audio/transcriptions",
    "openrouter-decisions": "/api/alpha/decisions",
    "openrouter-extractions": "/api/alpha/extractions",
    "openrouter-batch": "/api/v1/batches",
}
OPENROUTER_BATCH_ENDPOINTS = ("/v1/chat/completions", "/v1/responses", "/v1/messages", "/v1/embeddings")
_OPENROUTER_PROVIDER_TAG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*){0,3}")
_OPENROUTER_TURN_EFFORT_DOC = "https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#changing-effort-mid-conversation"
# The guide names these exact models. General reasoning metadata does not establish update support.
_OPENROUTER_TURN_EFFORT_MODELS = {
    "anthropic/claude-fable-5.1", "anthropic/claude-fable-5.2", "anthropic/claude-opus-5",
    "openai/gpt-6-astra", "openai/gpt-6-sol", "openai/gpt-6-luna",
    "openai/gpt-6.1-sol", "openai/gpt-6.1-terra",
}
_OPENROUTER_TURN_EFFORT_PROTOCOLS = {"openrouter-chat", "openai-responses", "openrouter-messages"}
PERPLEXITY_SERVICE_TIERS = ("auto", "default", "flex", "priority", "fast")
_PERPLEXITY_OPTIONAL_TIERS = {"flex", "priority"}
_PERPLEXITY_EFFORT_PRESETS = {"fast", "low", "medium", "high", "xhigh", "ultra"}
_ORCHESTRATOR_RESPONSES_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
ZED_NATIVE_PROTOCOLS = {
    "openrouter": "openrouter-chat", "openai": "openai-responses",
    "anthropic": "anthropic-messages", "google": "google-generate-content", "lm-studio": "openai-chat",
}
_ZED_PROVIDER_SOURCES = {
    "openrouter": "https://github.com/zed-industries/zed/blob/v1.28.1/crates/language_models/src/provider/open_router.rs",
    "openai": "https://github.com/zed-industries/zed/blob/v1.28.1/crates/open_ai/src/open_ai.rs#L296-L378",
    "anthropic": "https://github.com/zed-industries/zed/blob/v1.28.1/crates/anthropic/src/anthropic.rs#L140-L182",
    "google": "https://github.com/zed-industries/zed/blob/v1.28.1/crates/google_ai/src/google_ai.rs#L836-L883",
    "lm-studio": "https://github.com/zed-industries/zed/blob/v1.28.1/crates/language_models/src/provider/lmstudio.rs",
}
_LM_STUDIO_V0_MODEL_TYPES = {"llm": "llm", "vlm": "llm", "embeddings": "embedding"}
_ZED_GOOGLE_EXCLUDED = {
    "latest", "image", "banana", "tts", "audio", "live", "transcribe", "customtools",
    "computer", "robotics", "research", "omni", "embedding",
}
_ZED_GOOGLE_BUDGET_MODELS = {"gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"}
_ZED_GOOGLE_ZERO_BUDGET = _ZED_GOOGLE_BUDGET_MODELS - {"gemini-2.5-pro"}
_ZED_GOOGLE_MINIMAL = {"gemini-3-flash-preview", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash"}
ZED_SPEEDS = ("standard", "fast")
# Fast mode exposed by the built-in API providers in Zed v1.28.1.
_ZED_SPEED_MODELS = {
    "openai": {
        "gpt-4o-mini", "o3", "gpt-5", "gpt-5-mini", "gpt-5.1", "gpt-5.2", "gpt-5.3-codex",
        "gpt-5.4-mini", "gpt-5.4", "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
        "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol",
    },
    "anthropic": {"claude-opus-4-8", "claude-opus-5", "claude-opus-5-5"},
}
# Built-in OpenAI Responses model capabilities in Zed v1.28.1.
_ZED_OPENAI_LEVELS = {
    **dict.fromkeys(("gpt-4", "gpt-4o-mini"), ()),
    "o3": ("low", "medium", "high"),
    **dict.fromkeys(("gpt-5", "gpt-5-mini", "gpt-5-nano"), ("minimal", "low", "medium", "high")),
    "gpt-5.1": ("none", "low", "medium", "high"),
    "gpt-5.3-codex": ("low", "medium", "high", "xhigh"),
    **dict.fromkeys(("gpt-5.4-pro", "gpt-5.5-pro"), ("medium", "high", "xhigh")),
    **dict.fromkeys(("gpt-5.2", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.5"),
                    ("none", "low", "medium", "high", "xhigh")),
    **dict.fromkeys(("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-6-sol", "gpt-6-luna"),
                    ("none", "low", "medium", "high", "xhigh", "max")),
    **dict.fromkeys(("gpt-6-astra", "gpt-6.1-sol"), ("low", "medium", "high", "xhigh", "max")),
}
_OPENROUTER_BATCH_REASONING = {"/v1/chat/completions": "openrouter-chat", "/v1/responses": "openai-responses"}
_OPENROUTER_ENDPOINTS = {
    "openrouter-chat": "/api/v1/chat/completions",
    "openai-chat": "/api/v1/chat/completions",
    "openai-responses": "/api/v1/responses",
    **_OPENROUTER_TASK_ENDPOINTS,
}
_OPENROUTER_API_PATH = re.compile(r"/api/(?:v[1-9][0-9]*|alpha)/[a-z0-9-]+(?:/[a-z0-9-]+)*\Z")
_PROTOCOLS = {
    "openai-chat", "openai-responses", "anthropic-messages", "google-generate-content",
    "lmstudio-chat", "perplexity-agent", "openrouter-chat",
} | _LM_STUDIO_NONCHAT.keys() | _OPENROUTER_TASK_ENDPOINTS.keys()
_SECRET_KEYS = {"api_key", "apikey", "authorization", "credentials", "credential", "secret", "access_token",
                "refresh_token", "password", "headers", "apiKey", "token"}
_SECRET = re.compile(r"(?:sk-(?:or-|ant-|proj-)?[A-Za-z0-9_-]{8,}|(?:crsr|cog)_[A-Za-z0-9_-]{8,}|"
                     r"AIza[A-Za-z0-9_-]{15,}|gh[pousr]_[A-Za-z0-9_]{12,}|Bearer\s+[A-Za-z0-9._~-]{12,})",
                     re.IGNORECASE)
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_OPENROUTER_PRICE_FIELDS = {
    "prompt", "completion", "request", "image", "image_output", "image_token", "audio", "audio_output",
    "input_audio_cache", "input_cache_read", "input_cache_write", "input_cache_write_1h", "internal_reasoning", "web_search",
}
_OPENROUTER_OVERRIDE_PRICES = {
    "prompt", "completion", "audio", "input_audio_cache", "input_cache_read", "input_cache_write", "input_cache_write_1h",
}
_OPENROUTER_PRICE_CONDITIONS = {"min_prompt_tokens", "utc_start", "utc_end", "utc_days"}
_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
_DECIMAL_PRICE = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def _fail(message: str) -> NoReturn:
    raise WeaverError(message)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2000:
        _fail(f"{label}: non-empty bounded text required")
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF or c in "\u200b\u202e\u2066\u2067\u2068\u2069"
           for c in value) or _SECRET.search(value):
        _fail(f"{label}: unsafe or secret-like value")
    return value


def _model_id(value: object) -> str:
    identifier = _text(value, "model id")
    if len(identifier) > 250 or not _MODEL.fullmatch(identifier):
        _fail("Invalid model id")
    if identifier.casefold().startswith(("/", "sk-", "bearer")) or "//" in identifier or re.match(r"^[A-Za-z]:/", identifier):
        _fail("Invalid model id")
    if any(part in {"", ".", ".."} for part in identifier.split("/")):
        _fail("Invalid model id")
    return identifier


def _scan(value: object, depth: int = 0, *, allow_empty_headers: bool = False) -> None:
    """Reject secrets/non-finite data even in fields intentionally not retained."""
    if depth > 40:
        _fail("Model metadata is nested too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            if allow_empty_headers and key == "headers" and isinstance(item, dict) and not item:
                continue
            if not isinstance(key, str) or key.casefold().replace("-", "_") in _SECRET_KEYS:
                _fail("Credentials are not accepted in model metadata")
            _scan(item, depth + 1, allow_empty_headers=allow_empty_headers)
    elif isinstance(value, list):
        for item in value:
            _scan(item, depth + 1, allow_empty_headers=allow_empty_headers)
    elif isinstance(value, str):
        if _SECRET.search(value):
            _fail("Secret-like value in model metadata")
    elif isinstance(value, float) and not math.isfinite(value):
        _fail("Non-finite model metadata")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        _fail("Invalid model metadata value")


def _strings(value: object, label: str, *, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        _fail(f"{label}: expected list")
    result = [_text(item, label) for item in value]
    if len(result) != len(set(result)) or (allowed is not None and set(result) - allowed):
        _fail(f"{label}: duplicate or unsupported value")
    return result


def _read_snapshot_json(path: Path, label: str) -> tuple[object, str]:
    def read() -> bytes:
        try:
            with path.open("rb") as stream:
                content = stream.read(_MAX_SNAPSHOT_BYTES + 1)
        except OSError:
            raise WeaverError(f"Cannot read {label}") from None
        if len(content) > _MAX_SNAPSHOT_BYTES:
            raise WeaverError(f"{label} exceeds 32 MiB")
        return content

    data = read()
    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object,
                            parse_constant=_invalid_constant, parse_float=_json_float)
    except (UnicodeError, ValueError, RecursionError):
        raise WeaverError(f"Cannot read valid UTF-8 {label}") from None
    if read() != data:
        raise WeaverError(f"{label} changed while reading")
    return parsed, hashlib.sha256(data).hexdigest()


def _load_bounded_snapshot(path: Path, label: str, *, allow_empty_headers: bool = False) -> tuple[object, str]:
    parsed, digest = _read_snapshot_json(path, label)
    _scan(parsed, allow_empty_headers=allow_empty_headers)
    return parsed, digest


def orchestrator_function(
    protocol: str, name: str, description: str, schema: dict[str, Any],
) -> dict[str, Any]:
    fields = {"name": name, "description": description}
    if protocol == "anthropic-messages":
        return {**fields, "input_schema": schema}
    function = {**fields, "parameters": schema, "strict": False}
    if protocol == "openai-chat":
        return {"type": "function", "function": function}
    return {"type": "function", **function}


def orchestrator_mcp_tools(
    protocol: str, snapshots: dict[str, Path], selection: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    mapping: dict[str, dict[str, str]] = {}
    sources: dict[str, str] = {}
    for server, names in selection.items():
        if len(set(names)) != len(names):
            _fail("Duplicate Orchestrator MCP tool selection")
        parsed, sources[server] = _load_bounded_snapshot(snapshots[server], "MCP tool list")
        if isinstance(parsed, dict) and "jsonrpc" in parsed:
            if parsed.get("jsonrpc") != "2.0" or "error" in parsed:
                _fail("Expected a successful MCP JSON-RPC result")
            parsed = parsed.get("result")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("tools"), list):
            _fail("Expected a saved MCP tools/list result")
        listed: dict[str, dict[str, Any]] = {}
        for tool in parsed["tools"]:
            name = tool.get("name") if isinstance(tool, dict) else None
            if not isinstance(name, str) or not name or name in listed:
                _fail("MCP tool list contains an invalid or duplicate name")
            listed[name] = tool
        for name in names:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or name not in listed:
                _fail("Selected MCP tool is invalid or missing from the saved list")
            schema = listed[name].get("inputSchema")
            description = listed[name].get("description")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                _fail("Selected MCP tool requires an object inputSchema")
            if description is not None and not isinstance(description, str):
                _fail("Selected MCP tool description must be text")
            suffix = hashlib.sha256(f"{server}:{name}".encode("ascii")).hexdigest()[:16]
            prefix = f"{server}__{name}".replace(".", "_")[:46]
            alias = f"{prefix}__{suffix}"
            if alias in mapping:
                _fail("Orchestrator MCP tool names collide")
            mapping[alias] = {"server": server, "tool": name}
            declarations.append(orchestrator_function(
                protocol, alias, description or f"Call the selected MCP tool {name}.", schema,
            ))
    return declarations, {"protocol": protocol, "snapshot_sha256": sources, "functions": mapping}


def _reasoning(value: object) -> dict[str, Any]:
    if value is None:
        return {"state": "unknown", "efforts": None, "budget_tokens": None, "toggle": None, "modes": None}
    if not isinstance(value, dict) or set(value) - {"efforts", "budget_tokens", "toggle", "default", "on_value", "off_value", "modes"}:
        _fail("Invalid reasoning capability metadata")
    efforts = None if value.get("efforts") is None else _strings(value["efforts"], "reasoning efforts", allowed=_EFFORTS)
    budget = value.get("budget_tokens")
    if budget is not None:
        if not isinstance(budget, dict) or set(budget) - {"minimum", "maximum"}:
            _fail("Invalid reasoning token budget capability")
        minimum, maximum = budget.get("minimum", 1), budget.get("maximum")
        if type(minimum) is not int or minimum < 0 or (maximum is not None and (type(maximum) is not int or maximum < minimum)):
            _fail("Invalid reasoning token budget range")
        budget = {"minimum": minimum, "maximum": maximum}
    toggle = value.get("toggle")
    if toggle is not None and type(toggle) is not bool:
        _fail("Invalid reasoning toggle capability")
    modes = None if value.get("modes") is None else _strings(value["modes"], "reasoning modes", allowed={"standard", "pro"})
    result: dict[str, Any] = {"state": "declared", "efforts": efforts, "budget_tokens": budget, "toggle": toggle, "modes": modes}
    for key in ("default", "on_value", "off_value"):
        if key in value:
            result[key] = _text(value[key], "reasoning value")
    return result


def load_model_providers(root: Path) -> dict[str, dict[str, Any]]:
    """Read the owned provider catalog and validate every product/surface reference."""
    catalog = load_json(root / "catalogs" / "model-providers.json")
    if (set(catalog) != {"schema_version", "checked_on", "providers"} or
            type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1):
        _fail("Unsupported model provider catalog")
    try:
        date.fromisoformat(catalog["checked_on"])
    except (ValueError, TypeError):
        _fail("Invalid model provider evidence date")
    entries = catalog["providers"]
    if not isinstance(entries, list) or not entries:
        _fail("Model providers must be a non-empty list")
    integrations = load_integrations(root)
    result: dict[str, dict[str, Any]] = {}
    required = {"id", "integration_id", "surface", "base_url", "secret_env", "protocols", "discovery", "sources", "known_models"}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != required:
            _fail("Invalid model provider entry")
        identifier = _text(entry["id"], "provider id")
        if not _ID.fullmatch(identifier) or identifier in result:
            _fail("Invalid or duplicate model provider id")
        integration_id = _text(entry["integration_id"], "provider integration id")
        surface_id = _text(entry["surface"], "provider surface id")
        integration = integrations.get(integration_id)
        if integration is None or surface_id not in integration.surface_ids():
            _fail("Model provider must reference a registered integration surface")
        base = entry["base_url"]
        if not safe_https_url(base) and base != "http://localhost:1234":
            _fail("Invalid model provider base URL")
        secret = entry["secret_env"]
        if secret is not None and (not isinstance(secret, str) or not _ENV.fullmatch(secret)):
            _fail("Invalid credential environment reference")
        if secret is None and identifier != "lm-studio":
            _fail("Only the documented local optional-auth provider may omit a credential reference")
        protocols = _strings(entry["protocols"], "provider protocols", allowed=_PROTOCOLS)
        if not protocols:
            _fail("Provider requires a documented protocol")
        sources = _strings(entry["sources"], "provider sources")
        if not sources or any(not safe_https_url(source) for source in sources):
            _fail("Provider sources require credential-free HTTPS URLs")
        discovery = entry["discovery"]
        if not isinstance(discovery, dict) or set(discovery) != {"endpoint", "format", "auth", "scope", "pagination"}:
            _fail("Invalid discovery metadata")
        for field in discovery.values():
            _text(field, "discovery metadata")
        if discovery["format"] not in {"openrouter", "openai", "anthropic", "google", "lmstudio", "kilo-gateway"}:
            _fail("Unsupported snapshot format")
        known = entry["known_models"]
        if not isinstance(known, list):
            _fail("Invalid documented models")
        seen: set[str] = set()
        known_fields = {"id", "reasoning", "sources"}
        optional_fields = {"service_tiers"} if identifier == "perplexity-agent" else set()
        for model in known:
            if not isinstance(model, dict) or not known_fields <= model.keys() <= known_fields | optional_fields:
                _fail("Invalid documented model")
            if "service_tiers" in model and not _strings(model["service_tiers"], "Perplexity model service tiers",
                                                         allowed=_PERPLEXITY_OPTIONAL_TIERS):
                _fail("Documented service tiers must be non-empty")
            model_id = _model_id(model["id"])
            if model_id in seen:
                _fail("Duplicate documented model")
            seen.add(model_id)
            _reasoning(model["reasoning"])
            model_sources = _strings(model["sources"], "model sources")
            if not model_sources or any(not safe_https_url(source) for source in model_sources):
                _fail("Model evidence requires HTTPS sources")
        result[identifier] = {**entry, "checked_on": catalog["checked_on"]}
    return result


def load_model_hosts(root: Path) -> list[dict[str, Any]]:
    """Read documented host controls independently of API backend model catalogs.

    A manual row describes a documented picker, command or configuration field;
    it does not promise an exporter, account access or a tested installation.
    Null control values require discovery in the selected host/model, rather than
    borrowing an effort vocabulary from another provider or surface.
    """
    catalog = load_json(root / "catalogs" / "model-hosts.json")
    if (set(catalog) != {"schema_version", "hosts"} or
            type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1 or
            not isinstance(catalog["hosts"], list)):
        _fail("Unsupported model host catalog")
    _scan(catalog)
    registered = {(spec.id, surface.id) for spec in load_integrations(root).values() for surface in spec.surfaces}
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    required = {"product_id", "surface_id", "selection", "reasoning", "checked_on", "fixture_tests",
                "runtime_checks", "availability"}
    for row in catalog["hosts"]:
        if not isinstance(row, dict) or set(row) != required:
            _fail("Invalid model host entry")
        pair = (_text(row["product_id"], "host product id"), _text(row["surface_id"], "host surface id"))
        if pair not in registered or pair in seen:
            _fail("Model host must reference a unique registered product/surface")
        seen.add(pair)
        try:
            date.fromisoformat(row["checked_on"])
        except (ValueError, TypeError):
            _fail("Invalid model host evidence date")
        if (row["fixture_tests"] != ["tests/test_model_hosts.py"] or row["runtime_checks"] != [] or
                row["availability"] != "unknown"):
            _fail("Model host catalog must keep fixture evidence separate from unknown runtime availability")
        selection, reasoning = row["selection"], row["reasoning"]
        if not isinstance(selection, dict) or set(selection) != {"state", "mechanism", "fields", "commands", "ui_steps", "sources"}:
            _fail("Invalid host model selection metadata")
        if not isinstance(reasoning, dict) or set(reasoning) != {"state", "controls", "sources"}:
            _fail("Invalid host reasoning metadata")
        for section in (selection, reasoning):
            if not isinstance(section["state"], str) or section["state"] not in {"manual", "unknown", "unsupported"}:
                _fail("Invalid model host documentation state")
            sources = _strings(section["sources"], "host documentation sources")
            if (section["state"] == "manual" and not sources) or any(not safe_https_url(url) for url in sources):
                _fail("Documented host controls require credential-free HTTPS sources")
        _text(selection["mechanism"], "host selection mechanism")
        for key in ("fields", "commands", "ui_steps"):
            _strings(selection[key], f"host selection {key}")
        controls = reasoning["controls"]
        if not isinstance(controls, list) or (reasoning["state"] == "manual") != bool(controls):
            _fail("Host reasoning controls must match their documentation state")
        control_fields: set[tuple[str, str]] = set()
        for control in controls:
            if (not isinstance(control, dict) or set(control) != {"kind", "field", "values", "model_dependent"} or
                    not isinstance(control["kind"], str) or control["kind"] not in {"effort", "budget", "toggle", "mode", "variant"} or
                    control["model_dependent"] is not True):
                _fail("Invalid host reasoning control")
            field = _text(control["field"], "host reasoning field")
            control_key = (control["kind"], field)
            if control_key in control_fields:
                _fail("Duplicate host reasoning control")
            control_fields.add(control_key)
            if control["values"] is not None and not _strings(control["values"], "host reasoning values"):
                _fail("Known host reasoning values cannot be empty")
        result.append(row)
    if seen != registered:
        _fail("Model host catalog must include exactly one row per registered product/surface")
    return sorted(result, key=lambda row: (row["product_id"], row["surface_id"]))


def model_inventory(root: Path) -> dict[str, Any]:
    """Describe offline discovery routes, not an exhaustive model availability claim."""
    providers = load_model_providers(root)
    return {"schema_version": 1, "network_used": False, "credentials_read": False,
            "catalog_completeness": "unknown", "inference_tested": False,
            "providers": list(providers.values()), "hosts": load_model_hosts(root)}


def _native_reasoning(entry: dict[str, Any], format_name: str) -> dict[str, Any]:
    if format_name == "openrouter" and "reasoning" in entry:
        supplied = entry["reasoning"]
        if not isinstance(supplied, dict):
            _fail("Invalid OpenRouter reasoning metadata")
        for key in ("mandatory", "default_enabled", "supports_max_tokens"):
            if key in supplied and type(supplied[key]) is not bool:
                _fail("Invalid OpenRouter reasoning flags")
        efforts = None
        if "supported_efforts" in supplied:
            efforts = (sorted(_EFFORTS) if supplied["supported_efforts"] is None else
                       _strings(supplied["supported_efforts"], "OpenRouter efforts", allowed=_EFFORTS))
            if supplied.get("mandatory") is True:
                efforts = [effort for effort in efforts if effort != "none"]
        router_cap: dict[str, Any] = {"efforts": efforts, "toggle": supplied.get("mandatory") is False,
                                     "budget_tokens": {"minimum": 1, "maximum": None} if supplied.get("supports_max_tokens") is True else None}
        if supplied.get("default_effort") is not None:
            router_cap["default"] = supplied["default_effort"]
        return _reasoning(router_cap)
    if "reasoning" in entry:
        return _reasoning(entry["reasoning"])
    caps = entry.get("capabilities")
    if caps is not None and not isinstance(caps, dict):
        _fail("Invalid model capabilities")
    if isinstance(caps, dict) and format_name == "lmstudio" and "reasoning" in caps:
        reason = caps["reasoning"]
        if not isinstance(reason, dict):
            _fail("Invalid LM Studio reasoning capabilities")
        options = _strings(reason.get("allowed_options"), "LM Studio reasoning options",
                           allowed=_LM_STUDIO_REASONING_OPTIONS)
        default = reason.get("default")
        if default is not None and default not in options:
            _fail("Invalid LM Studio default reasoning option")
        return _reasoning({"efforts": [x for x in options if x not in {"off", "on"}],
                           "toggle": "off" in options and "on" in options,
                           **({"default": default} if default else {})})
    if isinstance(caps, dict) and format_name == "anthropic":
        effort = caps.get("effort")
        thinking = caps.get("thinking")
        declared: dict[str, Any] = {}
        if "effort" in caps and not isinstance(effort, dict):
            _fail("Invalid Anthropic effort capability")
        if isinstance(effort, dict):
            for descriptor in effort.values():
                if not isinstance(descriptor, dict) or type(descriptor.get("supported")) is not bool:
                    _fail("Invalid Anthropic effort support descriptor")
            choices = []
            for key in _EFFORTS:
                support = effort.get(key)
                if isinstance(support, dict) and support.get("supported") is True:
                    choices.append(key)
            declared["efforts"] = sorted(choices)
        if "thinking" in caps and (not isinstance(thinking, dict) or not isinstance(thinking.get("types"), dict)):
            _fail("Invalid Anthropic thinking capability")
        if isinstance(thinking, dict):
            types = thinking["types"]
            for descriptor in types.values():
                if not isinstance(descriptor, dict) or type(descriptor.get("supported")) is not bool:
                    _fail("Invalid Anthropic thinking support descriptor")
            enabled = types.get("adaptive", {}).get("supported") is True
            disabled = types.get("disabled", {}).get("supported") is True
            declared["toggle"] = enabled and disabled
            declared["on_value"] = "adaptive"
            declared["off_value"] = "disabled"
        if declared:
            return _reasoning(declared)
    return _reasoning(None)


def _snapshot_provenance(value: object, *, normalized: bool = False) -> dict[str, Any]:
    allowed = {"source_url", "captured_on", "scope", "auth_scope"}
    if normalized:
        allowed |= {"origin", "sha256", "discovery_scope", "auth_requirement"}
    if not isinstance(value, dict) or set(value) - allowed:
        _fail("Invalid snapshot provenance")
    for key, item in value.items():
        if key == "source_url":
            if not safe_https_url(item) and item != "http://localhost:1234/api/v1/models":
                _fail("Snapshot provenance needs a credential-free source URL")
        elif key == "captured_on":
            try:
                date.fromisoformat(item)
            except (ValueError, TypeError):
                _fail("Invalid snapshot capture date")
        elif key == "origin":
            if item != "caller-supplied":
                _fail("Invalid normalized snapshot origin")
        elif key == "sha256":
            if not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item):
                _fail("Invalid snapshot provenance hash")
        else:
            _text(item, "snapshot scope")
    return dict(value)


def _openrouter_pricing(value: object) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Preserve documented conditional prices without calculating a request cost.

    The observed -1 string is retained as an unresolved observation, never a
    negative charge or zero. Its undocumented meaning is deliberately not inferred.
    """
    if not isinstance(value, dict) or set(value) - (_OPENROUTER_PRICE_FIELDS | {"discount", "overrides"}):
        _fail("Invalid OpenRouter pricing schema")
    observations: list[dict[str, Any]] = []

    def amount(field: str, raw: object, override_index: int | None = None) -> str | None:
        if raw is None:
            return None
        if raw == "-1":
            observation: dict[str, Any] = {"field": field, "raw": "-1", "state": "unknown"}
            if override_index is not None:
                observation["override_index"] = override_index
            observations.append(observation)
            return None
        if not isinstance(raw, str) or not _DECIMAL_PRICE.fullmatch(_text(raw, "price")):
            _fail("Invalid OpenRouter price")
        try:
            parsed = Decimal(raw)
        except InvalidOperation:
            _fail("Invalid OpenRouter price")
        if not parsed.is_finite() or parsed < 0:
            _fail("Invalid OpenRouter price")
        return raw

    pricing: dict[str, Any] = {}
    for key, raw in value.items():
        if key in _OPENROUTER_PRICE_FIELDS:
            pricing[key] = amount(key, raw)
        elif key == "discount":
            if type(raw) not in {int, float} or not 0 <= raw <= 1:
                _fail("Invalid OpenRouter pricing discount")
            pricing[key] = raw
        else:
            if not isinstance(raw, list):
                _fail("OpenRouter pricing overrides must be a list")
            overrides: list[dict[str, Any]] = []
            for index, entry in enumerate(raw):
                if not isinstance(entry, dict) or set(entry) - (_OPENROUTER_OVERRIDE_PRICES | _OPENROUTER_PRICE_CONDITIONS):
                    _fail("Invalid OpenRouter price override schema")
                if ("utc_start" in entry) != ("utc_end" in entry):
                    _fail("OpenRouter pricing time windows require both bounds")
                override: dict[str, Any] = {}
                for field, item in entry.items():
                    if field in _OPENROUTER_OVERRIDE_PRICES:
                        override[field] = amount(field, item, index)
                    elif field == "min_prompt_tokens":
                        if type(item) is not int or item < 0:
                            _fail("Invalid OpenRouter pricing token threshold")
                        override[field] = item
                    elif field == "utc_days":
                        days = _strings(item, "OpenRouter pricing weekdays", allowed=_WEEKDAYS)
                        if not days:
                            _fail("OpenRouter pricing weekdays cannot be empty")
                        override[field] = days
                    else:
                        if type(item) is not int or not 0 <= item <= 2359 or item % 100 > 59:
                            _fail("Invalid OpenRouter pricing UTC time")
                        override[field] = item
                if "utc_start" in entry and entry["utc_start"] == entry["utc_end"]:
                    _fail("OpenRouter pricing time window bounds must differ")
                overrides.append(override)
            pricing[key] = overrides
    return pricing, observations


def _model_architecture(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"input_modalities", "output_modalities", "modality", "tokenizer", "instruct_type"}:
        _fail("Invalid model architecture metadata")
    result: dict[str, Any] = {}
    for field, item in value.items():
        if field in {"input_modalities", "output_modalities"}:
            result[field] = _strings(item, "model modalities")
        else:
            result[field] = None if item is None else _text(item, "model architecture")
    return result


def _openrouter_endpoints(value: object) -> list[str]:
    paths = _strings(value, "OpenRouter model endpoint paths")
    if any(not _OPENROUTER_API_PATH.fullmatch(path) for path in paths):
        _fail("Invalid OpenRouter model endpoint path")
    return paths


def _openrouter_catalog_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if "canonical_slug" in entry:
        metadata["canonical_slug"] = _model_id(entry["canonical_slug"])
    if "alias_target" in entry:
        alias = entry["alias_target"]
        if alias is not None:
            if not isinstance(alias, dict):
                _fail("Invalid OpenRouter alias target")
            alias = {"slug": _model_id(alias.get("slug")), "name": _text(alias.get("name"), "alias target name")}
        metadata["alias_target"] = alias
    if "supported_voices" in entry:
        voices = entry["supported_voices"]
        metadata["supported_voices"] = None if voices is None else _strings(voices, "OpenRouter voice ids")
    if "top_provider" in entry:
        top_provider = entry["top_provider"]
        if not isinstance(top_provider, dict):
            _fail("Invalid OpenRouter top provider metadata")
        top: dict[str, Any] = {}
        for key in ("context_length", "max_completion_tokens"):
            if key in top_provider:
                limit = top_provider[key]
                if limit is not None and (type(limit) is not int or limit < 0):
                    _fail("Invalid OpenRouter top provider token limit")
                top[key] = limit
        if "is_moderated" in top_provider:
            moderated = top_provider["is_moderated"]
            if type(moderated) is not bool:
                _fail("Invalid OpenRouter top provider moderation flag")
            top["is_moderated"] = moderated
        metadata["top_provider"] = top
    return metadata


def _lmstudio_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    """Keep model identity, installed variants, and instance state separate."""
    model_type = entry.get("type")
    variants = entry.get("variants")
    selected = entry.get("selected_variant")
    variant_ids = None if variants is None else [
        _model_id(value) for value in _strings(variants, "LM Studio variants")
    ]
    selected_id = None if selected is None else _model_id(selected)
    if selected_id is not None and variant_ids is not None and selected_id not in variant_ids:
        _fail("LM Studio selected variant is absent from the model's variants")
    instances: list[dict[str, Any]] | None = None
    if "loaded_instances" in entry:
        raw_instances = entry["loaded_instances"]
        if not isinstance(raw_instances, list):
            _fail("Invalid LM Studio loaded instance list")
        instances = []
        seen: set[str] = set()
        for instance in raw_instances:
            if not isinstance(instance, dict) or not isinstance(instance.get("config"), dict):
                _fail("Invalid LM Studio loaded instance")
            identifier = _model_id(instance.get("id"))
            context = instance["config"].get("context_length")
            if identifier in seen or (context is not None and (type(context) is not int or context < 1)):
                _fail("Duplicate LM Studio instance ID or invalid loaded context length")
            seen.add(identifier)
            instances.append({"id": identifier, "context_length": context})
    capabilities = entry.get("capabilities")
    native_reasoning = capabilities.get("reasoning") if isinstance(capabilities, dict) else None
    options = None
    if native_reasoning is not None:
        if not isinstance(native_reasoning, dict):
            _fail("Invalid LM Studio reasoning capabilities")
        options = _strings(native_reasoning.get("allowed_options"), "LM Studio reasoning options",
                           allowed=_LM_STUDIO_REASONING_OPTIONS)
    metadata: dict[str, Any] = {
        "type": None if model_type is None else _text(model_type, "LM Studio model type"),
        "variants": variant_ids, "selected_variant": selected_id,
        "loaded_instances": instances, "native_reasoning_options": options,
    }
    if model_type == "decision":
        if "decision_type" in entry:
            metadata["decision_type"] = _text(entry["decision_type"], "LM Studio decision model type")
        if "has_vision_adapter" in entry:
            has_vision_adapter = entry["has_vision_adapter"]
            if type(has_vision_adapter) is not bool:
                _fail("Invalid LM Studio decision vision adapter flag")
            metadata["has_vision_adapter"] = has_vision_adapter
    return metadata


def _openrouter_unresolved_pages(pages: list[dict[str, Any]], sizes: list[int]) -> bool:
    """Check captured offsets and reported totals without asserting global completeness."""
    first = pages[0]
    first_links = first.get("links", {})
    if first.get("offset", 0) or first_links.get("prev") or first_links.get("previous"):
        return True
    total = first.get("total_count")
    if total is None:
        return len(pages) > 1 or bool(first_links.get("next"))
    if total != sum(sizes) or any(page.get("total_count") != total for page in pages):
        return True
    if len(pages) > 1 and not all(sizes):
        return True
    offset = 0
    for page, size in zip(pages[:-1], sizes[:-1], strict=True):
        if page.get("offset", offset) != offset:
            return True
        offset += size
        next_link = page.get("links", {}).get("next")
        if not next_link:
            return True
        next_offsets = [value for key, value in parse_qsl(urlsplit(next_link).query) if key == "offset"]
        if next_offsets != [str(offset)]:
            return True
    last = pages[-1]
    return last.get("offset", offset) != offset or bool(last.get("links", {}).get("next"))


def _snapshot(raw: dict[str, Any], provider_id: str, provider: dict[str, Any], digest: str) -> dict[str, Any]:
    format_name = provider["discovery"]["format"]
    if format_name == "kilo-gateway":
        from .kilo_gateway_models import omit_catalog_descriptions

        raw = omit_catalog_descriptions(raw)
    _scan(raw)
    if raw.get("provider_id", provider_id) != provider_id:
        _fail("Snapshot provider does not match requested provider")
    model_detail = (provider_id == "perplexity-orchestrator" and raw.get("object") == "model"
                    and "pages" not in raw and "data" not in raw)
    pages = [{"data": [raw]}] if model_detail else raw.get("pages", [raw])
    if not isinstance(pages, list) or not pages:
        _fail("Snapshot pages must be a non-empty list")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_metadata: list[dict[str, Any]] = []
    page_sizes: list[int] = []
    legacy_lmstudio = (format_name == "lmstudio" and "pages" not in raw and "models" not in raw
                       and raw.get("object") == "list" and "data" in raw)
    for page in pages:
        if not isinstance(page, dict):
            _fail("Invalid model snapshot page")
        field = "models" if format_name in {"google", "lmstudio"} and not legacy_lmstudio else "data"
        entries = page.get(field)
        if not isinstance(entries, list):
            _fail(f"Snapshot requires {field} array")
        page_sizes.append(len(entries))
        page_info = {key: page[key] for key in ("has_more", "nextPageToken", "first_id", "last_id", "total_count", "offset", "limit") if key in page}
        if "has_more" in page_info and type(page_info["has_more"]) is not bool:
            _fail("Snapshot has_more must be boolean")
        for key in ("nextPageToken", "first_id", "last_id"):
            if key in page_info and page_info[key] is not None:
                _text(page_info[key], "pagination cursor")
        for key in ("total_count", "offset", "limit"):
            if key in page_info and (type(page_info[key]) is not int or page_info[key] < 0):
                _fail("Invalid numeric pagination metadata")
        if "links" in page:
            links = page["links"]
            if not isinstance(links, dict):
                _fail("Invalid pagination links")
            safe_links: dict[str, Any] = {}
            for label, link in links.items():
                if label not in {"next", "prev", "previous", "first", "last", "self"}:
                    _fail("Unsupported pagination link")
                if link is not None:
                    address = _text(link, "pagination link")
                    try:
                        parsed = urlsplit(address)
                        params = parse_qsl(parsed.query, strict_parsing=True)
                    except ValueError:
                        _fail("Invalid pagination link")
                    if (parsed.username or parsed.password or parsed.fragment or "\\" in address or
                            parsed.scheme not in {"", "https"} or
                            (parsed.netloc and parsed.hostname != "openrouter.ai") or
                            any(key.casefold() in {"key", "api_key", "token", "access_token", "secret"} for key, _ in params)):
                        _fail("Unsafe pagination link")
                safe_links[label] = link
            page_info["links"] = safe_links
        page_metadata.append(page_info)
        for entry in entries:
            if not isinstance(entry, dict):
                _fail("Every model snapshot entry must be an object")
            if legacy_lmstudio:
                model_type = entry.get("type")
                if (entry.get("object") != "model" or not isinstance(model_type, str)
                        or model_type not in _LM_STUDIO_V0_MODEL_TYPES):
                    _fail("Expected a typed LM Studio v0 model entry")
                entry = {"key": entry.get("id"), "type": _LM_STUDIO_V0_MODEL_TYPES[model_type],
                         "max_context_length": entry.get("max_context_length")}
            key = "name" if format_name == "google" else "key" if format_name == "lmstudio" else "id"
            identifier = _model_id(entry.get(key))
            if identifier in seen:
                _fail("Duplicate model id in snapshot")
            seen.add(identifier)
            display = entry.get("display_name", entry.get("displayName", entry.get("name", identifier)))
            item: dict[str, Any] = {"id": identifier, "display_name": _text(display, "model display name"),
                                    "reasoning": _native_reasoning(entry, format_name), "inference_tested": False}
            if format_name == "lmstudio":
                item["lm_studio"] = _lmstudio_metadata(entry)
            if format_name == "kilo-gateway":
                from .kilo_gateway_models import kilo_gateway_metadata

                item["kilo_gateway"] = kilo_gateway_metadata(entry)
                if "architecture" in entry:
                    item["architecture"] = _model_architecture(entry["architecture"])
            if format_name == "google" and "thinking" in entry:
                if type(entry["thinking"]) is not bool:
                    _fail("Invalid Gemini model thinking capability")
                item["google_thinking"] = entry["thinking"]
            if format_name == "anthropic" and "reasoning" not in entry and isinstance(entry.get("capabilities"), dict):
                thinking = entry["capabilities"].get("thinking")
                if isinstance(thinking, dict):
                    item["anthropic_adaptive_thinking"] = (
                        thinking.get("supported") is True and thinking["types"].get("adaptive", {}).get("supported") is True
                    )
            if format_name == "openrouter":
                item.update(_openrouter_catalog_metadata(entry))
                if "endpoints" in entry:
                    item["endpoints"] = _openrouter_endpoints(entry["endpoints"])
                if "architecture" in entry:
                    item["architecture"] = _model_architecture(entry["architecture"])
                if "reasoning" in entry:
                    item["reasoning_metadata"] = {key: entry["reasoning"][key] for key in (
                        "mandatory", "default_enabled", "supported_efforts", "default_effort", "supports_max_tokens")
                        if key in entry["reasoning"]}
            for target, alternatives in {
                "context_length": ("context_length", "contextWindow", "max_context_length"),
                "input_limit": ("inputTokenLimit", "max_input_tokens"),
                "output_limit": ("outputTokenLimit", "max_tokens"),
            }.items():
                for source in alternatives:
                    if source in entry and entry[source] is not None:
                        if type(entry[source]) is not int or entry[source] < 0:
                            _fail("Invalid model token limit")
                        item[target] = entry[source]
                        break
            if "supported_parameters" in entry:
                item["supported_parameters"] = _strings(entry["supported_parameters"], "supported parameters")
            if "supportedGenerationMethods" in entry:
                item["supported_methods"] = _strings(entry["supportedGenerationMethods"], "supported methods")
            # Preserve prices as supplied data; absent prices remain absent, never zero.
            if "pricing" in entry and format_name == "openrouter":
                item["pricing"], observations = _openrouter_pricing(entry["pricing"])
                if observations:
                    item["pricing_observations"] = observations
            elif "pricing" in entry:
                if not isinstance(entry["pricing"], dict):
                    _fail("Invalid model pricing")
                pricing: dict[str, Any] = {}
                for price_key, amount in entry["pricing"].items():
                    if not isinstance(price_key, str) or not re.fullmatch(r"[a-z_]+", price_key):
                        _fail("Invalid pricing key")
                    if format_name == "kilo-gateway" and price_key in {"prompt", "completion"} and amount == "-1":
                        item.setdefault("pricing_observations", []).append(
                            {"field": price_key, "raw": "-1", "state": "unknown"}
                        )
                        amount = None
                    if amount is not None and not isinstance(amount, (str, int, float)):
                        _fail("Invalid price")
                    if isinstance(amount, str):
                        if price_key == "unit":
                            _text(amount, "price unit")
                        else:
                            try:
                                number = float(amount)
                            except (ValueError, OverflowError):
                                _fail("Invalid price")
                            if not math.isfinite(number) or number < 0:
                                _fail("Invalid price")
                    elif amount is not None and (type(amount) is bool or amount < 0):
                        _fail("Invalid price")
                    pricing[price_key] = amount
                item["pricing"] = pricing
            models.append(item)
    provenance = _snapshot_provenance(raw.get("provenance", {}))
    incomplete = any(p.get("has_more") is True or p.get("nextPageToken") for p in page_metadata)
    if format_name == "openrouter":
        incomplete = incomplete or _openrouter_unresolved_pages(page_metadata, page_sizes)
    else:
        incomplete = incomplete or any(p.get("links", {}).get("next") for p in page_metadata)
    discovery_scope = ("LM Studio v0 model-list shape with model IDs and types" if legacy_lmstudio else
                       provider["discovery"]["scope"])
    return {"schema_version": 1, "provider_id": provider_id, "format": format_name,
            "models": models, "model_count": len(models), "pagination": {"pages": page_metadata,
            "page_count": len(pages), "has_unresolved_pages": bool(incomplete)},
            "completeness": "partial" if incomplete or model_detail else "unknown", "provenance": {
                "origin": "caller-supplied", "sha256": digest, "discovery_scope": discovery_scope,
                "auth_requirement": provider["discovery"]["auth"], **provenance},
            "network_used": False, "credentials_read": False, "inference_tested": False}


def read_model_snapshot(path: Path, provider_id: str, root: Path) -> dict[str, Any]:
    """Normalize every valid entry from a caller-supplied response or pages envelope."""
    provider = load_model_providers(root).get(provider_id)
    if provider is None:
        _fail("Unknown model provider")
    try:
        if path.stat().st_size > _MAX_SNAPSHOT_BYTES:
            _fail("Model snapshot exceeds 32 MiB")
        data = path.read_bytes()
    except OSError:
        _fail("Cannot read model snapshot")
    if len(data) > _MAX_SNAPSHOT_BYTES:
        _fail("Model snapshot exceeds 32 MiB")
    raw = load_json(path)
    # Refuse a changing input instead of assigning the wrong provenance hash.
    try:
        if path.read_bytes() != data:
            _fail("Model snapshot changed while reading")
    except OSError:
        _fail("Cannot recheck model snapshot")
    return _snapshot(raw, provider_id, provider, hashlib.sha256(data).hexdigest())


def read_openrouter_provider_endpoints(path: Path) -> dict[str, Any]:
    """Inspect provider-level metadata from a saved model-specific endpoint listing."""
    raw, digest = _load_bounded_snapshot(path, "OpenRouter provider endpoint snapshot")
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("endpoints"), list):
        _fail("Expected an OpenRouter model-specific response with data.endpoints")
    model = _model_id(data.get("id"))
    endpoints = []
    for item in data["endpoints"]:
        if not isinstance(item, dict) or _model_id(item.get("model_id")) != model:
            _fail("OpenRouter provider endpoint model ID must match the response")
        tag = _text(item.get("tag"), "OpenRouter provider tag")
        if not _OPENROUTER_PROVIDER_TAG.fullmatch(tag):
            _fail("Invalid OpenRouter provider tag")
        parameters = item.get("supported_parameters")
        limits = {}
        for field in ("context_length", "max_prompt_tokens", "max_completion_tokens"):
            if (limit := item.get(field)) is not None:
                if type(limit) is not int or limit < 0:
                    _fail("Invalid OpenRouter provider endpoint token limit")
                limits[field] = limit
        status = item.get("status")
        if status is not None and type(status) is not int:
            _fail("Invalid OpenRouter provider endpoint status")
        endpoints.append({"tag": tag, "provider_name": _text(item.get("provider_name"), "OpenRouter provider name"),
                          "supported_parameters": (None if parameters is None else
                                                   _strings(parameters, "endpoint supported parameters")),
                          "status": status, **limits})
    return {"schema_version": 1, "provider_id": "openrouter", "model_id": model,
            "provider_endpoints": endpoints, "endpoint_count": len(endpoints), "sha256": digest,
            "network_used": False, "credentials_read": False, "inference_tested": False, "availability": "unknown"}


def _wire(protocol: str, cap: dict[str, Any], *, effort: str | None, budget_tokens: int | None,
          reasoning_enabled: bool | None, mode: str | None = None) -> dict[str, Any]:
    if effort is None and budget_tokens is None and reasoning_enabled is None and mode is None:
        return {}
    if mode is not None and (cap["modes"] is None or mode not in cap["modes"] or protocol not in {"openai-responses", "openrouter-chat"}):
        _fail("Requested reasoning mode is unsupported or unknown for this model/protocol")
    if effort is not None and (effort not in _EFFORTS or cap["efforts"] is None or effort not in cap["efforts"]):
        _fail("Requested effort is unsupported or unknown for this model")
    if budget_tokens is not None:
        budget = cap["budget_tokens"]
        if type(budget_tokens) is not int or budget is None or budget_tokens < budget["minimum"] or (
                budget["maximum"] is not None and budget_tokens > budget["maximum"]):
            _fail("Requested reasoning budget is unsupported or outside the model range")
    if reasoning_enabled is not None and (type(reasoning_enabled) is not bool or cap["toggle"] is not True):
        _fail("Reasoning toggle support is unknown or unsupported for this model")
    if reasoning_enabled is False and (effort is not None or budget_tokens is not None or mode is not None):
        _fail("Disabled reasoning conflicts with an effort, budget or mode")
    if reasoning_enabled is True and effort == "none":
        _fail("Enabled reasoning conflicts with none effort")
    if protocol == "openrouter-chat":
        if effort is not None and budget_tokens is not None:
            _fail("OpenRouter reasoning effort and token budget cannot be combined")
        options: dict[str, Any] = {}
        for key, value in (("effort", effort), ("max_tokens", budget_tokens), ("enabled", reasoning_enabled), ("mode", mode)):
            if value is not None:
                options[key] = value
        return {"reasoning": options}
    if protocol == "lmstudio-chat":
        if budget_tokens is not None:
            _fail("LM Studio native chat does not document a reasoning token budget")
        return {"reasoning": effort or ("on" if reasoning_enabled else "off")}
    if protocol in {"anthropic-messages", "openrouter-messages"}:
        if protocol == "openrouter-messages":
            if effort in {"minimal", "none"}:
                _fail("OpenRouter Messages has no matching output_config.effort value")
            if effort is not None and budget_tokens is not None:
                _fail("OpenRouter Messages effort and token budget cannot be combined")
        payload: dict[str, Any] = {}
        if effort is not None:
            payload["output_config"] = {"effort": effort}
        if budget_tokens is not None:
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}
        elif reasoning_enabled is not None:
            thinking_type = cap.get("on_value", "adaptive") if reasoning_enabled else cap.get("off_value", "disabled")
            if thinking_type not in ({"adaptive", "enabled", "between_tools"} if reasoning_enabled else {"disabled"}):
                _fail("Invalid Anthropic reasoning toggle wire mapping")
            payload["thinking"] = {"type": thinking_type}
        return payload
    if protocol == "google-generate-content":
        config: dict[str, Any] = {}
        if effort is not None:
            config["thinkingLevel"] = effort
        if budget_tokens is not None:
            if effort is not None:
                _fail("Google thinking level and budget cannot be combined")
            config["thinkingBudget"] = budget_tokens
        if reasoning_enabled is not None:
            if effort is not None or budget_tokens is not None:
                _fail("Google reasoning toggle conflicts with level or budget")
            config["thinkingBudget"] = -1 if reasoning_enabled else 0
        return {"generationConfig": {"thinkingConfig": config}}
    if budget_tokens is not None:
        _fail("This protocol has no documented reasoning token budget")
    selected = effort
    if reasoning_enabled is not None and selected is None:
        selected = cap.get("on_value") if reasoning_enabled else cap.get("off_value")
        if selected is None:
            _fail("No documented wire mapping for the reasoning toggle")
        if (selected not in _EFFORTS or
                (reasoning_enabled and (selected == "none" or cap["efforts"] is None or selected not in cap["efforts"])) or
                (not reasoning_enabled and selected != "none")):
            _fail("Reasoning toggle wire mapping is unsupported by the declared model effort subset")
    if protocol == "openai-chat":
        return {"reasoning_effort": selected}
    options = {}
    if selected is not None:
        options["effort"] = selected
    if mode is not None:
        options["mode"] = mode
    return {"reasoning": options}


def check_model(root: Path, provider_id: str, model_id: str, snapshot: Mapping[str, Any] | None = None, *,
                effort: str | None = None, budget_tokens: int | None = None,
                reasoning_enabled: bool | None = None, protocol: str | None = None,
                mode: str | None = None, context_size: int | None = None,
                batch_endpoint: str | None = None) -> dict[str, Any]:
    """Validate an explicit ID and requested reasoning without asserting availability."""
    providers = load_model_providers(root)
    provider = providers.get(provider_id)
    if provider is None:
        _fail("Unknown model provider")
    identifier = _model_id(model_id)
    if context_size is not None and (type(context_size) is not int or context_size < 1):
        _fail("Context size must be a positive integer")
    chosen = protocol or provider["protocols"][0]
    if chosen not in provider["protocols"]:
        _fail("Protocol is not documented for this provider")
    router_task = chosen in _OPENROUTER_TASK_ENDPOINTS
    router_endpoint = _OPENROUTER_ENDPOINTS.get(chosen) if provider_id == "openrouter" else None
    batch = chosen == "openrouter-batch"
    batch_model = identifier.removesuffix(":batch") if batch else identifier
    expected_id = f"{batch_model}:batch" if batch else identifier
    if batch:
        _model_id(batch_model)
        if batch_model.endswith(":batch"):
            _fail("Expected an OpenRouter model slug with at most one :batch suffix")
        if batch_endpoint not in OPENROUTER_BATCH_ENDPOINTS:
            _fail("OpenRouter batch staging requires a documented --batch-endpoint")
        if any(value is not None for value in (budget_tokens, reasoning_enabled, mode, context_size)):
            _fail("Batch staging supports an explicit effort only for Chat Completions or Responses")
        if effort is not None and batch_endpoint not in _OPENROUTER_BATCH_REASONING:
            _fail("Batch effort requires the Chat Completions or Responses endpoint")
    elif batch_endpoint is not None:
        _fail("--batch-endpoint requires the openrouter-batch protocol")
    if router_task:
        if router_endpoint is None or snapshot is None:
            _fail("This OpenRouter route requires a saved OpenRouter model list with endpoint metadata")
        if chosen == "openrouter-messages" and context_size is not None:
            _fail("OpenRouter Messages request staging does not accept a context-size override")
        if chosen not in {"openrouter-batch", "openrouter-messages"} and any(
                value is not None for value in (effort, budget_tokens, reasoning_enabled, mode, context_size)):
            _fail("This OpenRouter route stages only the model selector")
    nonchat = _LM_STUDIO_NONCHAT.get(chosen)
    if nonchat is not None:
        if provider_id != "lm-studio" or snapshot is None:
            _fail("This LM Studio route requires a saved native model list")
        if any(value is not None for value in (effort, budget_tokens, reasoning_enabled, mode, context_size)):
            _fail("This LM Studio route does not accept chat reasoning or context controls")
    cap = _reasoning(None)
    evidence: dict[str, Any] = {"origin": "unknown", "sources": []}
    for known in provider["known_models"]:
        if known["id"] == identifier:
            cap = _reasoning(known["reasoning"])
            evidence = {"origin": "documentation", "sources": known["sources"], "checked_on": provider["checked_on"]}
    visible: bool | None = None
    provenance: dict[str, Any] | None = None
    architecture: dict[str, Any] | None = None
    endpoints: list[str] | None = None
    supported_parameters: list[str] | None = None
    openrouter_metadata: dict[str, Any] = {}
    anthropic_adaptive_thinking: bool | None = None
    lmstudio_options: list[str] | None = None
    lmstudio_instance: dict[str, Any] | None = None
    if snapshot is not None:
        if not isinstance(snapshot, Mapping) or snapshot.get("provider_id") != provider_id:
            _fail("Snapshot provider does not match requested provider")
        _scan(dict(snapshot))
        entries = snapshot.get("models")
        if not isinstance(entries, list):
            _fail("Expected a normalized snapshot from read_model_snapshot")
        seen: set[str] = set()
        visible = False
        nonchat_matches = 0
        lmstudio_matches = 0
        for entry in entries:
            if not isinstance(entry, dict):
                _fail("Invalid normalized snapshot")
            entry_id = _model_id(entry.get("id"))
            if entry_id in seen:
                _fail("Duplicate model id in snapshot")
            seen.add(entry_id)
            declared_architecture = _model_architecture(entry["architecture"]) if "architecture" in entry else None
            if nonchat is not None:
                metadata = entry.get("lm_studio")
                if not isinstance(metadata, dict) or metadata.get("type") != nonchat[0]:
                    continue
                if nonchat[0] == "embedding":
                    nonchat_matches += entry_id == identifier
                else:
                    instances = metadata.get("loaded_instances")
                    if isinstance(instances, list):
                        nonchat_matches += sum(isinstance(instance, dict) and instance.get("id") == identifier
                                               for instance in instances)
                continue
            instance = None
            if provider_id == "lm-studio":
                metadata = entry.get("lm_studio")
                if isinstance(metadata, dict) and isinstance(metadata.get("loaded_instances"), list):
                    matches = [row for row in metadata["loaded_instances"]
                               if isinstance(row, dict) and row.get("id") == identifier]
                    if len(matches) > 1:
                        _fail("Ambiguous LM Studio loaded instance ID")
                    instance = matches[0] if matches else None
            if entry_id == expected_id or instance is not None:
                visible = True
                if provider_id == "lm-studio":
                    lmstudio_matches += 1
                    if lmstudio_matches > 1:
                        _fail("Ambiguous LM Studio model key or loaded instance ID")
                architecture = declared_architecture
                if provider_id == "anthropic" and "anthropic_adaptive_thinking" in entry:
                    adaptive = entry["anthropic_adaptive_thinking"]
                    if type(adaptive) is not bool:
                        _fail("Invalid Anthropic adaptive thinking capability")
                    anthropic_adaptive_thinking = adaptive
                if router_endpoint is not None and "endpoints" in entry:
                    endpoints = _openrouter_endpoints(entry["endpoints"])
                if provider_id == "openrouter":
                    openrouter_metadata = _openrouter_catalog_metadata(entry)
                    if "supported_parameters" in entry:
                        supported_parameters = _strings(entry["supported_parameters"], "supported parameters")
                if provider_id == "lm-studio" and "lm_studio" in entry:
                    metadata = entry["lm_studio"]
                    if not isinstance(metadata, dict):
                        _fail("Invalid LM Studio snapshot metadata")
                    model_type = metadata.get("type")
                    if model_type is not None and model_type != "llm":
                        _fail("Selected LM Studio model is not declared as a chat LLM")
                    if metadata.get("native_reasoning_options") is not None:
                        lmstudio_options = _strings(metadata["native_reasoning_options"], "LM Studio reasoning options",
                                                     allowed=_LM_STUDIO_REASONING_OPTIONS)
                    if instance is not None and entry_id != identifier:
                        loaded_context = instance.get("context_length")
                        if loaded_context is not None and (type(loaded_context) is not int or loaded_context < 1):
                            _fail("Invalid LM Studio loaded context length")
                        lmstudio_instance = {"model_key": entry_id, "context_length": loaded_context}
                        if context_size is None:
                            context_size = loaded_context
                if "context_length" in entry:
                    context = entry["context_length"]
                    if type(context) is not int or context < 0:
                        _fail("Invalid model context size in snapshot")
                    # A provider-declared zero does not establish a usable limit.
                    if context_size is None and context > 0 and lmstudio_instance is None:
                        context_size = context
                supplied = entry.get("reasoning")
                if isinstance(supplied, dict) and supplied.get("state") != "unknown":
                    cap = _reasoning({k: v for k, v in supplied.items() if k != "state"})
                    evidence = {"origin": "caller-supplied-snapshot", "sources": []}
        if snapshot.get("provenance") is not None:
            provenance = _snapshot_provenance(snapshot["provenance"], normalized=True)
        if nonchat is not None:
            if nonchat_matches != 1:
                _fail("Expected one matching LM Studio embedding key or loaded decision instance ID")
            visible = True
        if provider_id == "perplexity-orchestrator" and not visible:
            _fail("The saved Perplexity Orchestrator catalog does not list the requested model")
    if router_endpoint is not None:
        if endpoints is not None and router_endpoint not in endpoints:
            _fail("The selected model does not advertise the requested OpenRouter endpoint")
        if router_task and endpoints is None:
            _fail("Expected a matching OpenRouter model with explicit endpoint metadata")
    if batch and architecture is not None and "output_modalities" in architecture:
        output = "embeddings" if batch_endpoint == "/v1/embeddings" else "text"
        if output not in architecture["output_modalities"]:
            _fail("The OpenRouter batch variant does not advertise the requested output modality")
    lmstudio_switch = chosen in {"lmstudio-chat", "anthropic-messages"} or (
        reasoning_enabled is False and chosen in {"openai-responses", "openai-chat"}
    )
    payload: dict[str, Any]
    if (provider_id, chosen) == ("perplexity-orchestrator", "anthropic-messages"):
        if (any(value is not None for value in (effort, budget_tokens, mode)) or
                (reasoning_enabled is not None and reasoning_enabled is not False)):
            _fail("Perplexity Orchestrator Messages accepts only disabled thinking as an explicit reasoning control")
        payload = {"thinking": {"type": "disabled"}} if reasoning_enabled is False else {}
    elif lmstudio_switch and lmstudio_options is not None and reasoning_enabled is not None:
        if type(reasoning_enabled) is not bool:
            _fail("Reasoning toggle must be boolean")
        requested = "on" if reasoning_enabled else "off"
        if requested not in lmstudio_options or any(value is not None for value in (effort, budget_tokens, mode)):
            _fail("LM Studio reasoning switch is unsupported or conflicts with another reasoning control")
        lmstudio_payloads: dict[str, dict[str, Any]] = {
            "lmstudio-chat": {"reasoning": requested},
            "anthropic-messages": {"thinking": {"type": "adaptive" if reasoning_enabled else "disabled"}},
            "openai-responses": {"reasoning": {"effort": "none"}},
            "openai-chat": {"reasoning_effort": "none"},
        }
        payload = lmstudio_payloads[chosen]
    else:
        if chosen == "lmstudio-chat" and effort is not None and (
                effort not in {"low", "medium", "high"} or
                (lmstudio_options is not None and effort not in lmstudio_options)):
            _fail("Unsupported LM Studio native reasoning effort")
        reasoning_protocol = _OPENROUTER_BATCH_REASONING.get(batch_endpoint, chosen) if batch_endpoint else chosen
        if (provider_id, reasoning_protocol) == ("perplexity-agent", "openai-responses"):
            reasoning_protocol = "perplexity-agent"
        if provider_id == "openrouter" and reasoning_protocol == "openai-responses":
            reasoning_protocol = "openrouter-chat"
        if provider_id == "perplexity-orchestrator" and mode is not None:
            _fail("Perplexity Orchestrator does not document a reasoning mode field")
        payload = _wire(reasoning_protocol, cap, effort=effort, budget_tokens=budget_tokens,
                        reasoning_enabled=reasoning_enabled, mode=mode)
        if (provider_id, chosen) == ("perplexity-orchestrator", "openai-responses") and "reasoning" in payload:
            if payload["reasoning"]["effort"] not in _ORCHESTRATOR_RESPONSES_EFFORTS:
                _fail("This effort is outside the Perplexity Orchestrator Responses request schema")
    partial_orchestrator_catalog = (provider_id == "perplexity-orchestrator" and snapshot is not None
                                   and snapshot.get("completeness") == "partial")
    return {"provider_id": provider_id, "integration_id": provider["integration_id"], "surface": provider["surface"],
            "model_id": identifier, "protocol": chosen, "accepted_explicit_id": True, "visible_in_snapshot": visible,
            "inference_tested": False, "runtime_verified": False, "availability": "unknown",
            "reasoning_capability": cap, "reasoning_evidence": evidence, "reasoning_payload": payload,
            "snapshot_provenance": provenance,
            "catalog_completeness": "partial" if partial_orchestrator_catalog else "unknown", "network_used": False,
            "credentials_read": False, "context_size": context_size, "architecture": architecture,
            "advertised_endpoints": endpoints, "supported_parameters": supported_parameters,
            **({"anthropic_adaptive_thinking": anthropic_adaptive_thinking}
               if anthropic_adaptive_thinking is not None else {}),
            "batch_model": batch_model if batch else None, "batch_endpoint": batch_endpoint,
            "language_agent_compatibility": "unknown", **openrouter_metadata,
            **({"lm_studio_loaded_instance": lmstudio_instance} if lmstudio_instance is not None else {})}


def _json_artifact(path: str, value: object, kind: str) -> RenderedArtifact:
    return RenderedArtifact(PurePosixPath(path), (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode(), kind)


def _openrouter_turn_effort(checked: dict[str, Any], effort: str) -> dict[str, Any]:
    model = checked["model_id"]
    if model not in _OPENROUTER_TURN_EFFORT_MODELS:
        _fail("OpenRouter has no recorded mid-conversation effort support for this exact model")
    protocol = checked["protocol"]
    if not checked["visible_in_snapshot"] or _OPENROUTER_ENDPOINTS[protocol] not in (checked["advertised_endpoints"] or []):
        _fail("Mid-conversation effort requires a saved OpenRouter catalog that lists the model and endpoint")
    if model.startswith("anthropic/"):
        if effort == "none":
            _fail("Claude does not accept none in a mid-conversation effort update")
        if effort == "minimal":
            effort = "low"
    fields = _wire(protocol, checked["reasoning_capability"], effort=effort, budget_tokens=None, reasoning_enabled=None)
    if protocol == "openai-responses":
        return {"type": "configuration_update", **fields}
    if protocol == "openrouter-chat":
        return {"role": "system", "content": "", "configuration_update": fields}
    return {"role": "system", "content": [], **fields}


def render_cursor_cli_model(model: str) -> list[RenderedArtifact]:
    """Stage an explicit Cursor account model as one argument value."""
    identifier = _model_id(model)
    return [
        _json_artifact("cursor/model/session-arguments.json", ["--model", identifier], "session-arguments"),
        RenderedArtifact(PurePosixPath("cursor/model/model-setup.md"), (
            "# Cursor CLI model setup\n\n"
            "Run agent --list-models in your signed-in CLI to confirm the exact model ID. "
            "Pass the staged argument list to agent for one session. Account and team settings "
            "control availability. Weaver stages these arguments offline.\n\n"
            "- https://cursor.com/docs/cli/reference/parameters\n"
        ).encode(), "model-setup"),
    ]


_ANTIGRAVITY_GEMINI_SETUP = (
    "Review and merge the staged Gemini provider setting into ~/.gemini/antigravity-cli/settings.json. "
    "Supply GEMINI_API_KEY privately in the intended process. For a compatible custom endpoint, set "
    "GOOGLE_GEMINI_BASE_URL in that process. The fragment contains only the selected settings.\n\n"
)


def render_antigravity_adc(surface: str) -> list[RenderedArtifact]:
    if surface not in {"desktop", "cli"}:
        _fail("Antigravity ADC staging supports the App and CLI")
    prefix = PurePosixPath("antigravity", surface, "provider")
    notes = [
        "# Antigravity Application Default Credentials", "",
        "Use the Google Cloud project and credentials approved for your Antigravity session.",
        "Review `launch-env.json` and set its variable only for the intended Antigravity process.",
        "The Google Cloud SDK and any required credential provisioning are separate from this bundle.",
    ]
    artifacts = [
        _json_artifact(str(prefix / "launch-env.json"), {"AGY_ADC_AUTH": "true"}, "provider-environment"),
    ]
    if surface == "desktop":
        artifacts.append(_json_artifact(str(prefix / "config.fragment.json"),
                                        {"userSettings": {"enableAdc": True}}, "provider-config"))
        notes.extend([
            "Alternatively, review and merge only `userSettings.enableAdc` into `~/.gemini/config/config.json`.",
            "The App reads the location from its ADC configuration or uses the documented default.",
            "Choose a model and reasoning level in the account's model picker.",
        ])
    else:
        notes.extend([
            "Review any `GOOGLE_CLOUD_QUOTA_PROJECT` and `GOOGLE_CLOUD_LOCATION` values for the CLI process.",
            "Confirm the model slug and any reasoning option with `agy models` under the intended credentials.",
        ])
    notes.extend([
        "The artifacts contain mode settings only. Account access and inference require separate verification.", "",
        "- https://antigravity.google/docs/enterprise/#setting-up-application-default-credentials-adc",
        "- https://antigravity.google/docs/install/",
        "",
    ])
    artifacts.append(RenderedArtifact(prefix / "adc-setup.md", "\n".join(notes).encode(), "provider-setup"))
    return artifacts


def render_antigravity_cli_model(
    model: str, effort: str | None, *, custom_alias: str | None = None,
    reasoning_enabled: bool | None = None, context_size: int | None = None, gemini_api: bool = False,
) -> list[RenderedArtifact]:
    """Stage an explicit Antigravity CLI model selector without resolving account models."""
    if custom_alias is not None:
        return _antigravity_custom_model(model, custom_alias, effort, reasoning_enabled, context_size)
    if reasoning_enabled is not None or context_size is not None:
        _fail("Thinking and context controls require an Antigravity CLI custom model")
    identifier = _model_id(model)
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", identifier):
        _fail("Antigravity CLI requires an exact model slug from agy models")
    arguments = ["--model", identifier]
    if effort is not None:
        efforts = {"low", "medium", "high", "xhigh", "max"}
        if effort not in efforts:
            _fail("Unsupported Antigravity CLI effort")
        suffix = identifier.rpartition("-")[2]
        if suffix in efforts and suffix != effort:
            _fail("Antigravity CLI model variant conflicts with the requested effort")
        arguments.extend(("--effort", effort))
    setup = _ANTIGRAVITY_GEMINI_SETUP if gemini_api else ""
    provider_source = "- https://antigravity.google/docs/install/#using-a-gemini-api-key\n" if gemini_api else ""
    artifacts = [
        _json_artifact("antigravity/model/session-arguments.json", arguments, "session-arguments"),
        RenderedArtifact(PurePosixPath("antigravity/model/model-setup.md"), (
            "# Antigravity CLI model setup\n\n"
            f"{setup}"
            "Confirm the exact slug with agy models in the intended account or provider mode. "
            "Pass the staged arguments to agy for one session. A slug with an effort suffix requires "
            "a matching flag. For a base slug, verify the selected effort in that CLI session. "
            "Account availability requires separate verification.\n\n"
            "- https://antigravity.google/docs/cli/reference\n"
            "- https://antigravity.google/docs/cli/commands/effort\n"
            f"{provider_source}"
        ).encode(), "model-setup"),
    ]
    if gemini_api:
        artifacts.insert(0, _json_artifact("antigravity/model/settings.fragment.json",
                                           {"modelProvider": "gemini"}, "model-config"))
    return artifacts


def _antigravity_custom_model(
    model: str, alias: str, effort: str | None, thinking: bool | None, context: int | None,
) -> list[RenderedArtifact]:
    model_id = model.removeprefix("models/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model_id):
        _fail("Antigravity custom models require a Gemini model ID or models/<id>")
    if not _ID.fullmatch(alias) or len(alias) > 64:
        _fail("Antigravity custom model aliases require a lowercase slug up to 64 characters")
    if effort is not None and (effort not in {"low", "medium", "high"} or thinking is False):
        _fail("Antigravity CLI 1.5.9 custom models require thinking enabled for low, medium or high effort")
    if context is not None and (type(context) is not int or context <= 65536):
        _fail("Antigravity CLI 1.5.9 custom model context must exceed its 65536-token output cap")
    entry: dict[str, Any] = {"modelName": f"models/{model_id}", "label": alias}
    if context is not None:
        entry["maxTokens"] = context
    if thinking is not None:
        entry["modelFeatures"] = {"supportsThinking": thinking}
    settings = {"modelProvider": "gemini", "customModelsConfig": {"customModels": {alias: entry}}}
    arguments = ["--model", alias]
    if effort is not None:
        arguments.extend(("--effort", effort))
    return [
        _json_artifact("antigravity/model/settings.fragment.json", settings, "model-config"),
        _json_artifact("antigravity/model/session-arguments.json", arguments, "session-arguments"),
        RenderedArtifact(PurePosixPath("antigravity/model/model-setup.md"), (
            "# Antigravity CLI Gemini custom model\n\n"
            f"{_ANTIGRAVITY_GEMINI_SETUP}"
            "Check for an existing alias or label in agy models before merging the custom model entry.\n\n"
            "Windows CLI 1.5.9 sent the correct model path with models/<id> and the endpoint set separately. "
            "Verify the request path before using a different build. Check that your endpoint honors the "
            "selected thinking level, tool capabilities, and context. An explicit context must accommodate "
            "the CLI's 65536-token output cap plus the conversation and tool instructions. "
            "Omitted fields use the CLI's defaults.\n\n"
            "- https://antigravity.google/docs/install?tab=cli\n"
            "- https://antigravity.google/docs/settings?tab=cli\n"
            "- https://antigravity.google/schemas/v1/settings.json\n"
        ).encode(), "model-setup"),
    ]


def render_kiro_cli_model(
    model: str, effort: str | None, *, persistent: bool = False,
    thinking_mode: str | None = None, thinking_display: str | None = None,
    max_output_tokens: int | None = None, acp: bool = False,
) -> list[RenderedArtifact]:
    """Stage Kiro service model settings using the documented V3 controls."""
    identifier = _model_id(model)
    if "/" in identifier or identifier.startswith(("~", "-")):
        _fail("Kiro requires a Kiro model service ID")
    claude_efforts = {"low", "medium", "high", "max"}
    extended_claude = {"claude-opus-5", "claude-opus-4.8", "claude-opus-4.7", "claude-sonnet-5"}
    standard_claude = {"claude-opus-4.6", "claude-sonnet-4.6"}
    sonnet_between_tools = identifier == "claude-sonnet-5.5"
    gpt_models = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
    if identifier in gpt_models or identifier in extended_claude or (sonnet_between_tools and thinking_mode == "adaptive"):
        allowed = claude_efforts | {"xhigh"}
    elif sonnet_between_tools:
        # Without an explicit adaptive mode the model may use between-tools thinking.
        allowed = {"low", "medium", "high"}
    elif identifier in standard_claude:
        allowed = claude_efforts
    else:
        allowed = set()
    if persistent and identifier in gpt_models:
        allowed.add("none")
    if effort is not None and effort not in allowed:
        _fail("Requested effort is unsupported or unknown for this Kiro CLI model")
    if acp and persistent:
        _fail("Kiro ACP session options and saved per-model defaults require separate outputs")
    if not persistent and any(value is not None for value in (thinking_mode, thinking_display, max_output_tokens)):
        _fail("Kiro per-model settings require --kiro-model-defaults")
    if thinking_display is not None and (thinking_mode != "adaptive" or thinking_display not in {"summarized", "omitted"}):
        _fail("Kiro thinking display requires adaptive thinking and summarized or omitted visibility")
    if thinking_mode is not None:
        modes = {"adaptive", "between_tools"} if sonnet_between_tools else (
            {"adaptive", "disabled"} if identifier in extended_claude | standard_claude else set()
        )
        if thinking_mode not in modes:
            _fail("Requested thinking mode is unsupported or unknown for this Kiro CLI model")
    if max_output_tokens is not None:
        large_output_models = gpt_models | {"claude-opus-5", "claude-opus-4.8", "claude-sonnet-5"}
        bounded_models = extended_claude | standard_claude | {"claude-sonnet-5.5"} | gpt_models
        if identifier not in bounded_models:
            _fail("The output token limit is unknown for this Kiro CLI model")
        upper = 128_000 if identifier in large_output_models else 64_000
        if type(max_output_tokens) is not int or not 1024 <= max_output_tokens <= upper:
            _fail(f"Kiro CLI output tokens must be between 1024 and {upper} for {identifier}")

    if acp:
        options = {"modelId": identifier}
        if effort is not None:
            options["effortLevel"] = effort
        handoff = (
            "# Kiro CLI V3 ACP model setup\n\n"
            "Pass the staged launch arguments to the existing kiro-cli executable. Complete ACP initialization "
            "and authentication with your client. Merge the staged options into session/new params with an "
            "absolute workspace cwd and your reviewed mcpServers array. Use an empty array for no client-supplied servers.\n\n"
            "Check the returned configuration options for the effective model and effort. Subsequent changes "
            "use session/set_config_option with advertised model and effortLevel values. Account availability "
            "and authorization are determined by Kiro.\n\n"
            "- https://kiro.dev/docs/cli/v3/acp-migration/#3-create-discover-and-load-sessions\n"
        )
        return [
            _json_artifact("kiro/model/acp-launch-arguments.json", ["acp", "--agent-engine=v3"], "acp-launch-arguments"),
            _json_artifact("kiro/model/acp-session-options.json", {"_meta": {"kiro": options}}, "acp-session-options"),
            RenderedArtifact(PurePosixPath("kiro/model/model-setup.md"), handoff.encode(), "model-setup"),
        ]
    if persistent:
        settings: dict[str, dict[str, str] | int] = {}
        if effort is not None:
            field = "reasoning" if identifier in gpt_models else "output_config"
            settings[field] = {"effort": effort}
        if thinking_mode is not None:
            thinking = {"type": thinking_mode}
            if thinking_display is not None:
                thinking["display"] = thinking_display
            settings["thinking"] = thinking
        if max_output_tokens is not None:
            settings["max_tokens"] = max_output_tokens
        if not settings:
            _fail("Kiro per-model defaults require an explicit reasoning effort, thinking mode, or output token limit")
        fragment = {"chat.modelDefaults": {identifier: settings}}
        output = _json_artifact("kiro/model/cli.fragment.json", fragment, "model-config")
        instructions = (
            "Review and merge only the selected model entry under chat.modelDefaults in "
            ".kiro/settings/cli.json for this workspace or ~/.kiro/settings/cli.json for this user. "
            "Preserve other model settings and entries. Remove a stale thinking.display when switching away from "
            "adaptive, and review any saved effort when disabling thinking. The session can override model defaults."
        )
    else:
        arguments = ["chat", "--v3", "--model", identifier]
        if effort is not None:
            arguments.extend(["--effort", effort])
        output = _json_artifact("kiro/model/session-arguments.json", arguments, "session-arguments")
        instructions = "Pass these arguments to kiro-cli to select the model and optional effort for one V3 session."
    handoff = (
        "# Kiro CLI V3 model setup\n\n"
        f"{instructions}\n\n"
        "Confirm the model and effort in Kiro's /model picker. Account and region determine availability. "
        "Weaver only stages files and performs no authentication, client launch, or inference.\n\n"
        "- https://kiro.dev/docs/models/effort/\n"
        "- https://kiro.dev/docs/cli/headless/\n"
    )
    return [output, RenderedArtifact(PurePosixPath("kiro/model/model-setup.md"), handoff.encode(), "model-setup")]


def _hermes_model_config(checked: Mapping[str, Any], api_base: str, effort: str | None,
                         context_size: int | None) -> dict[str, Any] | None:
    architecture = checked["architecture"]
    if architecture is not None and "text" not in architecture.get("output_modalities", ("text",)):
        return None
    provider, protocol, model = checked["provider_id"], checked["protocol"], checked["model_id"]
    if (provider, protocol) == ("lm-studio", "openai-chat"):
        selected_effort = checked["reasoning_payload"].get("reasoning_effort")
        if selected_effort is not None and selected_effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            return None
        config: dict[str, Any] = {
            "provider": "lmstudio", "default": model, "base_url": api_base, "api_mode": "chat_completions",
        }
        if context_size is not None:
            config["context_length"] = context_size
        result: dict[str, Any] = {"model": config}
        if selected_effort is not None:
            result["agent"] = {"reasoning_overrides": {f"lmstudio/{model}": selected_effort}}
        return result
    if (provider, protocol) == ("openrouter", "openrouter-chat") and context_size is None:
        result = {"model": {"provider": "openrouter", "default": model, "base_url": "",
                                          "api_mode": "chat_completions"}}
        if effort is not None:
            result["agent"] = {"reasoning_overrides": {f"openrouter/{model}": effort}}
        return result
    return None


def hermes_profile_model(root: Path, provider_id: str, model_id: str, snapshot: Mapping[str, Any] | None,
                         effort: str | None, context_size: int | None) -> dict[str, Any]:
    protocol = {"lm-studio": "openai-chat", "openrouter": "openrouter-chat"}.get(provider_id)
    if protocol is None:
        _fail("Hermes profile model staging supports the OpenRouter and LM Studio providers")
    checked = check_model(root, provider_id, model_id, snapshot, effort=effort, protocol=protocol,
                          context_size=context_size)
    api_base = load_model_providers(root)[provider_id]["base_url"]
    if provider_id == "lm-studio":
        api_base += "/v1"
    config = _hermes_model_config(checked, api_base, effort, context_size)
    if config is None:
        _fail("Hermes profile requires a text model and documented model settings for the selected provider")
    return config


def _perplexity_request_defaults(
    preset: str | None, profile: str | None, profile_version: str | None,
) -> tuple[dict[str, Any], list[str]]:
    if preset is not None and profile is not None:
        _fail("Choose a Perplexity effort preset or a saved profile")
    if profile is None and profile_version is not None:
        _fail("A Perplexity profile version requires a saved profile ID")
    if preset is not None:
        name, separator, version = _text(preset, "Perplexity effort preset").partition("@")
        if name not in _PERPLEXITY_EFFORT_PRESETS or (
            separator and (not version or "@" in version or any(char.isspace() for char in version))
        ):
            _fail("Invalid Perplexity effort preset or version")
        return {"effort": preset}, [
            "The effort preset supplies Perplexity's model, reasoning and tools unless overridden in the request.",
            "Bare and @latest selectors follow the active version. Confirm a pinned version exists in the target API.",
            "- https://docs.perplexity.ai/docs/agent-api/effort",
        ]
    if profile is not None:
        identifier = _text(profile, "Perplexity profile ID")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
            _fail("Invalid Perplexity profile ID")
        selector = {"type": "custom", "id": identifier}
        if profile_version is not None:
            version = _text(profile_version, "Perplexity profile version")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", version):
                _fail("Invalid Perplexity profile version")
            selector["version"] = version
        return {"profile": selector}, [
            "The saved profile supplies model, reasoning, tools, Skills and connectors unless overridden in the request.",
            "Omitting the profile version or choosing latest follows the active saved version.",
            "Confirm that the target project has access to the referenced profile and version.",
            "- https://docs.perplexity.ai/docs/agent-api/profiles",
        ]
    return {}, []


def _zed_openai_reasoning(model: str, effort: str | None, enabled: bool | None) -> dict[str, Any]:
    if effort is None and enabled is None:
        return {}
    if enabled is not None and type(enabled) is not bool:
        _fail("Reasoning toggle must be boolean")
    if (enabled is False and effort is not None) or (enabled is True and effort == "none"):
        _fail("Conflicting Zed OpenAI reasoning controls")
    levels = _ZED_OPENAI_LEVELS.get(model, ())
    selected = "none" if enabled is False else effort
    if (selected is not None and selected not in levels) or (enabled is True and not levels):
        _fail("Zed 1.28.1 does not document this reasoning setting for the built-in OpenAI model")
    return {"reasoning": {"effort": selected}} if selected is not None else {}


def _zed_google_settings(
    model: str, snapshot: Mapping[str, Any] | None, checked: Mapping[str, Any], *,
    effort: str | None, enabled: bool | None, budget: int | None, context: int | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    fields: dict[str, Any] = {}
    if snapshot is None:
        if any(value is not None for value in (effort, enabled, budget, context)):
            _fail("Zed Gemini thinking and context settings require a saved Google model catalog")
        return fields, {}, None
    row = next((row for row in snapshot["models"] if row["id"] == f"models/{model}"), None)
    if (row is None or not re.fullmatch(r"gemini-[0-9]+(?:\.[0-9]+)*-.+", model)
            or _ZED_GOOGLE_EXCLUDED.intersection(model.split("-"))
            or "generateContent" not in row.get("supported_methods", ())
            or any(type(row.get(key)) is not int or row[key] <= 0 for key in ("input_limit", "output_limit"))):
        _fail("The saved Google catalog must list a versioned Gemini text model that Zed can discover")
    if all(value is None for value in (effort, enabled, budget, context)):
        return fields, {}, None
    if row.get("google_thinking") is not True and not (budget is not None and model in _ZED_GOOGLE_BUDGET_MODELS):
        _fail("The saved Gemini model must advertise thinking")
    if enabled is not None and type(enabled) is not bool:
        _fail("Reasoning toggle must be boolean")
    if (enabled is False and (effort is not None or budget is not None)) or (effort is not None and budget is not None):
        _fail("Conflicting Zed Gemini thinking settings")
    config: dict[str, Any] = {}
    provider_model = None
    if context is not None and budget is None:
        _fail("Zed Gemini context overrides require a model thinking budget")
    if budget is not None:
        limits = checked["reasoning_capability"]["budget_tokens"]
        if (model not in _ZED_GOOGLE_BUDGET_MODELS or limits is None or type(budget) is not int
                or not max(1, limits["minimum"]) <= budget < min(row["output_limit"], 2**32)
                or (limits["maximum"] is not None and budget > limits["maximum"])):
            _fail("Zed Gemini requires a documented positive budget below the model output limit")
        if context is not None and context > row["input_limit"]:
            _fail("Zed Gemini context override exceeds the catalog input limit")
        provider_model = {"name": model, "display_name": row.get("display_name", model),
                          "max_tokens": context or row["input_limit"],
                          "mode": {"type": "thinking", "budget_tokens": budget}}
        config["thinkingBudget"] = budget
    elif effort is not None:
        levels = {"low", "medium", "high"}
        if model in _ZED_GOOGLE_MINIMAL:
            levels.add("minimal")
        declared = checked["reasoning_capability"]["efforts"]
        if model in _ZED_GOOGLE_BUDGET_MODELS or effort not in levels or (declared is not None and effort not in declared):
            _fail("The Gemini effort is unsupported by this Zed build and model combination")
        fields["effort"] = effort
        config["thinkingLevel"] = effort.upper()
    if enabled is False:
        if model in _ZED_GOOGLE_ZERO_BUDGET:
            config["thinkingBudget"] = 0
        elif model not in _ZED_GOOGLE_BUDGET_MODELS:
            config["thinkingLevel"] = "MINIMAL" if model in _ZED_GOOGLE_MINIMAL else "LOW"
    else:
        config["includeThoughts"] = True
    fields["enable_thinking"] = enabled is not False
    return fields, {"generationConfig": {"thinkingConfig": config}} if config else {}, provider_model


def render_model_setup(spec: IntegrationSpec, surface: str, *, root: Path, provider_id: str, model_id: str | None,
                       snapshot: Mapping[str, Any] | None = None, effort: str | None = None,
                       budget_tokens: int | None = None, reasoning_enabled: bool | None = None,
                       protocol: str | None = None, mode: str | None = None,
                       context_size: int | None = None, batch_endpoint: str | None = None,
                       zed_subagent: bool = False, zed_profile: str | None = None, zed_speed: str | None = None,
                       perplexity_fallbacks: list[str] | None = None,
                       perplexity_max_output_tokens: int | None = None,
                       perplexity_max_steps: int | None = None,
                       perplexity_service_tier: str | None = None,
                       perplexity_effort_preset: str | None = None, perplexity_profile: str | None = None,
                       perplexity_profile_version: str | None = None,
                       openrouter_provider_tag: str | None = None,
                       openrouter_provider_endpoints: Path | None = None,
                       openrouter_turn_effort: str | None = None) -> list[RenderedArtifact]:
    """Stage a reviewed fragment only for documented routes; otherwise give a manual handoff."""
    registered = get_integration(spec.id, root)
    registered.surface(surface)
    if openrouter_turn_effort is not None:
        if ((registered.id, surface, provider_id) != ("openrouter", "gateway", "openrouter")
                or protocol is not None and protocol not in _OPENROUTER_TURN_EFFORT_PROTOCOLS):
            _fail("Mid-conversation effort requires OpenRouter gateway Chat Completions, Responses or Messages")
        if mode == "pro" or batch_endpoint is not None:
            _fail("OpenRouter mid-conversation effort cannot be combined with pro mode or Batch")
    if openrouter_provider_tag is not None or openrouter_provider_endpoints is not None:
        if ((registered.id, surface, provider_id) != ("openrouter", "gateway", "openrouter")
                or protocol not in {None, "openrouter-chat", "openai-responses"}):
            _fail("OpenRouter provider filtering requires gateway Chat Completions or Responses")
        if openrouter_provider_tag is None or openrouter_provider_endpoints is None:
            _fail("OpenRouter provider filtering requires a tag and a saved provider endpoint listing")
    perplexity_agent = (registered.id, surface, provider_id) == ("perplexity", "agent-api", "perplexity-agent")
    perplexity_openai = perplexity_agent and protocol == "openai-responses"
    perplexity_endpoint = "/v1/responses" if perplexity_openai else "/v1/agent"
    if not perplexity_agent and any(value is not None for value in (
        perplexity_fallbacks, perplexity_max_output_tokens, perplexity_max_steps, perplexity_service_tier,
        perplexity_effort_preset, perplexity_profile, perplexity_profile_version,
    )):
        _fail("Perplexity request options require the Perplexity Agent API route")
    if perplexity_service_tier is not None and perplexity_service_tier not in PERPLEXITY_SERVICE_TIERS:
        _fail("Unsupported Perplexity Agent API service tier")
    service_tier = "priority" if perplexity_service_tier == "fast" else perplexity_service_tier
    if perplexity_max_output_tokens is not None and (
        type(perplexity_max_output_tokens) is not int or not 1 <= perplexity_max_output_tokens <= 2**31 - 1
    ):
        _fail("Perplexity max_output_tokens must be a positive 32-bit integer")
    if perplexity_max_steps is not None and (type(perplexity_max_steps) is not int or not 1 <= perplexity_max_steps <= 500):
        _fail("Perplexity max_steps must be an integer from 1 to 500")
    defaults, defaults_notes = _perplexity_request_defaults(
        perplexity_effort_preset, perplexity_profile, perplexity_profile_version,
    )
    if perplexity_openai:
        defaults_notes.extend([
            "Set the OpenAI SDK base URL to `https://api.perplexity.ai/v1` and supply the Perplexity API key separately.",
            "Pass the reviewed fields as `extra_body` to `client.responses.create` and supply `input` separately. "
            "Include any reviewed Skill and MCP fragments in the same `extra_body`.",
        ])
    request_overrides: dict[str, Any] = {}
    if perplexity_max_output_tokens is not None:
        request_overrides["max_output_tokens"] = perplexity_max_output_tokens
    if perplexity_max_steps is not None:
        request_overrides["max_steps"] = perplexity_max_steps
        defaults_notes.extend([
            "The explicit `max_steps` sets the research-loop ceiling. Allow separate steps to load a Skill and act on it.",
            "- https://docs.perplexity.ai/api-reference/agent-post",
        ])
    if service_tier is not None:
        request_overrides["service_tier"] = service_tier
    if defaults:
        defaults_notes.append("Review inherited tool and connector access. Request tools merge with the saved defaults.")
    if service_tier is not None:
        defaults_notes.extend(["Check the response's `service_tier` for the processing tier actually used.",
                               "- https://docs.perplexity.ai/docs/agent-api/models#service-tiers"])
    if model_id is None:
        if not defaults:
            _fail("Model staging requires --model, a Perplexity effort preset, or a saved Perplexity profile")
        if protocol not in (None, "perplexity-agent", "openai-responses") or zed_subagent or any(value is not None for value in (
            snapshot, effort, budget_tokens, reasoning_enabled, mode, context_size,
            batch_endpoint, perplexity_fallbacks, zed_profile, zed_speed,
        )):
            _fail("Perplexity model-specific options require an explicit model")
        prefix = f"{registered.id}/model"
        notes = ["# Model setup: Perplexity", "",
                 f"Merge the staged fields into a reviewed `POST {perplexity_endpoint}` request. "
                 "Supply input and credentials separately.",
                 "Effective model and optional tier depend on the resolved preset or profile. "
                 "Project access, resolved settings and inference were not verified.",
                 *defaults_notes]
        if perplexity_openai:
            notes.append("- https://docs.perplexity.ai/docs/agent-api/openai-compatibility")
        return [
            RenderedArtifact(PurePosixPath(f"{prefix}/model-setup.md"),
                             ("\n".join(notes) + "\n").encode(), "model-setup"),
            _json_artifact(f"{prefix}/model-selection.json", defaults | request_overrides, "model-selection"),
        ]
    if protocol in _LM_STUDIO_NONCHAT and (registered.id, surface) != ("lm-studio", "api"):
        _fail("Non-chat LM Studio selections require the LM Studio API surface")
    if protocol in _OPENROUTER_TASK_ENDPOINTS and (registered.id, surface) != ("openrouter", "gateway"):
        _fail("OpenRouter task endpoint selections require the OpenRouter gateway surface")
    if protocol is None and provider_id == "lm-studio" and (registered.id, surface) in {
        ("copilot", "cli"), ("copilot", "desktop"), ("openhands", "canvas")
    }:
        protocol = "openai-chat"
    zed_protocol = ZED_NATIVE_PROTOCOLS.get(provider_id) if (registered.id, surface) == ("zed", "ide") else None
    zed_lmstudio = zed_protocol == "openai-chat" and provider_id == "lm-studio" and protocol in (None, zed_protocol)
    if zed_lmstudio:
        if any(value is not None for value in (effort, reasoning_enabled, budget_tokens, mode, context_size)):
            _fail("Zed's built-in LM Studio provider stages a model ID without reasoning or context overrides")
        protocol = zed_protocol
    zed_google = zed_protocol == "google-generate-content" and protocol in (None, zed_protocol)
    zed_native = (zed_protocol is not None and protocol in (None, zed_protocol)
                  and not model_id.endswith(":batch")
                  and mode is None and batch_endpoint is None
                  and (zed_google or (budget_tokens is None and context_size is None)))
    zed_openai = zed_native and provider_id == "openai"
    if zed_google and not model_id.startswith("models/"):
        model_id = f"models/{model_id}"
    zed_custom_reasoning = zed_openai or (zed_native and zed_google)
    checked = check_model(root, provider_id, model_id, snapshot, effort=None if zed_custom_reasoning else effort,
                          budget_tokens=None if zed_google and zed_native else budget_tokens,
                          reasoning_enabled=None if zed_custom_reasoning else reasoning_enabled,
                          protocol=protocol, mode=mode, context_size=context_size, batch_endpoint=batch_endpoint)
    turn_update = _openrouter_turn_effort(checked, openrouter_turn_effort) if openrouter_turn_effort is not None else None
    provider = load_model_providers(root)[provider_id]
    endpoint_snapshot = None
    if openrouter_provider_tag is not None and openrouter_provider_endpoints is not None:
        if snapshot is not None and not checked["visible_in_snapshot"]:
            _fail("The saved OpenRouter catalog does not list the selected model")
        endpoint_snapshot = read_openrouter_provider_endpoints(openrouter_provider_endpoints)
        if endpoint_snapshot["model_id"] != model_id:
            _fail("OpenRouter provider endpoint listing is for a different model")
        matching = [item for item in endpoint_snapshot["provider_endpoints"] if item["tag"] == openrouter_provider_tag]
        if not matching:
            _fail("The saved OpenRouter endpoint listing does not contain the requested exact provider tag")
        if (checked["reasoning_payload"] or turn_update) and not any(
            "reasoning" in (row["supported_parameters"] or []) for row in matching
        ):
            _fail("The selected OpenRouter provider tag does not advertise the reasoning parameter")
    perplexity_chain = None
    if perplexity_fallbacks is not None:
        perplexity_chain = [model_id, *(_model_id(model) for model in perplexity_fallbacks)]
        if not 2 <= len(perplexity_chain) <= 5 or len(set(perplexity_chain)) != len(perplexity_chain):
            _fail("Perplexity fallback requires two to five distinct model IDs including the primary")
        if snapshot is None:
            documented = {entry["id"] for entry in provider["known_models"]}
            if not documented.issuperset(perplexity_chain):
                _fail("Perplexity fallback requires documented models or a saved Agent API catalog")
        elif not checked["visible_in_snapshot"]:
            _fail("The saved Perplexity Agent API catalog must list every model in the fallback chain")
        for fallback in perplexity_chain[1:]:
            alternative = check_model(root, provider_id, fallback, snapshot, effort=effort,
                                      budget_tokens=budget_tokens, reasoning_enabled=reasoning_enabled,
                                      protocol=protocol, mode=mode, context_size=context_size)
            if snapshot is not None and not alternative["visible_in_snapshot"]:
                _fail("The saved Perplexity Agent API catalog must list every model in the fallback chain")
            if alternative["reasoning_payload"] != checked["reasoning_payload"]:
                _fail("Perplexity fallback requires the same request-level reasoning payload for each model")
    if service_tier in ("flex", "priority"):
        tier_models = {entry["id"] for entry in provider["known_models"] if service_tier in entry.get("service_tiers", ())}
        if not tier_models.issuperset(perplexity_chain or [model_id]):
            _fail("The requested service tier needs Perplexity documentation for every selected model")
    prefix = f"{registered.id}/model"
    chosen = checked["protocol"]
    lmstudio_api_chat = (registered.id, surface, provider_id, chosen) == (
        "lm-studio", "api", "lm-studio", "lmstudio-chat",
    )
    if (registered.id, surface, provider_id) == ("lm-studio", "api", "lm-studio") and context_size is not None:
        if not lmstudio_api_chat:
            _fail("LM Studio API context-size staging requires native chat")
        instance = checked.get("lm_studio_loaded_instance")
        if instance and instance["context_length"] not in (None, context_size):
            _fail("Requested context differs from the captured LM Studio instance. Select the downloaded model key or reload the instance.")
    router_responses_extra_controls = (provider_id, chosen) == ("openrouter", "openai-responses") and (
        budget_tokens is not None or reasoning_enabled is not None
    )
    api_base = provider["base_url"]
    if zed_lmstudio:
        api_base += "/api/v0"
        if snapshot is not None and not checked["visible_in_snapshot"]:
            _fail("The saved LM Studio catalog does not list the selected Zed model")
    elif provider_id == "lm-studio" and (chosen in {"openai-chat", "openai-responses"} or chosen in _LM_STUDIO_NONCHAT):
        api_base += "/v1"
    elif perplexity_openai:
        api_base += "/v1"
    elif chosen == "anthropic-messages":
        # Anthropic SDK clients append /v1/messages to their configured base URL.
        api_base = api_base.removesuffix("/v1")
    native = False
    artifacts: list[RenderedArtifact] = []
    native_note = "No native model configuration exporter is documented for this product/surface/provider route."
    if perplexity_agent:
        native_note = "The model-selection fragment contains reusable Perplexity Agent API request fields."
    architecture = checked["architecture"]
    text_candidate = architecture is None or "output_modalities" not in architecture or "text" in architecture["output_modalities"]
    zed_fragment = zed_native and text_candidate and chosen == zed_protocol
    if zed_speed is not None:
        if zed_speed not in ZED_SPEEDS:
            _fail("Zed speed must be standard or fast")
        if not zed_fragment or provider_id not in _ZED_SPEED_MODELS:
            _fail("Zed speed staging requires a native Zed IDE OpenAI or Anthropic model")
        if model_id not in _ZED_SPEED_MODELS[provider_id]:
            _fail("Zed 1.28.1 does not document fast-mode support for the selected model")
        if snapshot is not None and not checked["visible_in_snapshot"]:
            _fail("The saved provider catalog does not list the selected Zed fast-mode model")
    zed_google_fields: dict[str, Any] = {}
    zed_google_provider_model = None
    zed_model_id = model_id.removeprefix("models/") if zed_google else model_id
    if zed_fragment and zed_openai:
        checked["reasoning_payload"] = _zed_openai_reasoning(model_id, effort, reasoning_enabled)
    if zed_fragment and zed_google:
        zed_google_fields, checked["reasoning_payload"], zed_google_provider_model = _zed_google_settings(
            zed_model_id, snapshot, checked, effort=effort, enabled=reasoning_enabled,
            budget=budget_tokens, context=context_size,
        )
    zed_parameters = checked["supported_parameters"]
    if zed_protocol == chosen == "openrouter-chat" and (effort is not None or reasoning_enabled is not None) and (
        zed_parameters is not None and "reasoning" not in zed_parameters
    ):
        _fail("The saved OpenRouter model does not advertise the reasoning parameter used by Zed")
    if zed_fragment and provider_id == "anthropic" and (effort is not None or reasoning_enabled is not None):
        if not checked["visible_in_snapshot"] or checked["reasoning_evidence"]["origin"] != "caller-supplied-snapshot":
            _fail("Zed Anthropic reasoning requires model capability data from a saved Anthropic catalog")
        if effort in {"none", "minimal"}:
            _fail("Zed Anthropic reasoning has no matching named effort")
        if checked.get("anthropic_adaptive_thinking") is not True:
            _fail("Zed Anthropic staged reasoning requires adaptive thinking in the saved model catalog")
    if zed_subagent and not zed_fragment:
        _fail("Zed subagent settings require a supported native Zed IDE model without budget, mode or context overrides")
    if zed_profile is not None:
        if zed_subagent or not zed_fragment:
            _fail("Zed profile settings require a supported native Zed IDE model without other target or tuning overrides")
        if len(zed_profile) > 64 or not _ID.fullmatch(zed_profile) or zed_profile in {"ask", "minimal", "write"}:
            _fail("Zed profile ID must be a distinct lowercase identifier of up to 64 letters, digits or hyphens")
    if not text_candidate:
        native_note = ("The supplied architecture does not declare text output. Use a task-specific manual handoff; "
                       "language-agent compatibility is unknown.")
    air_provider = {"openrouter": "OpenRouter", "anthropic": "Anthropic", "openai": "OpenAI", "google": "Gemini"}.get(
        provider_id
    )
    air_ide_model = text_candidate and (registered.id, surface) == ("jetbrains-air", "ide") and air_provider is not None
    air_desktop_lmstudio = text_candidate and (
        (registered.id, surface, provider_id) == ("jetbrains-air", "desktop", "lm-studio")
    )
    if air_ide_model:
        native_note = (
            f"In AI > Air > Accounts > More Providers, configure {air_provider} with the IDE's private form. "
            "Choose that provider in the session authorization menu for a compatible agent. "
            f"Look for `{model_id}` in that session's model picker and check the Effort control for the selected model. "
            "The protocol, API base, credential environment reference, and JSON fields here describe the provider "
            "API. Air manages its own connection. Confirm effort labels in the agent's picker."
        )
    if air_desktop_lmstudio:
        native_note = (
            "Configure LM Studio in an ACP-compatible agent that supports the selected protocol. "
            "Choose that agent in standalone Air and confirm the models and controls it exposes over ACP. "
            "The model-selection JSON describes the LM Studio API request fields."
        )
    canvas_model = text_candidate and (registered.id, surface) == ("openhands", "canvas") and (
        (provider_id, chosen) in {("openrouter", "openrouter-chat"), ("lm-studio", "openai-chat")}
    )
    if canvas_model:
        model_prefix = "openrouter/" if provider_id == "openrouter" else "openai/"
        native_note = (
            f"On the intended backend, open Settings > LLM > Advanced. Enter the model "
            f"`{model_prefix}{model_id}` and a backend-reachable base URL (reference `{api_base}`). "
            "Supply credentials separately. Saving can contact the provider. Select the saved profile "
            "for the next OpenHands conversation."
        )
        native_note += (
            " In the All tab, set `api_mode` to `chat` on backends that expose this field so a "
            "model that defaults to Responses uses the selected Chat Completions route."
        )
        if effort is not None:
            native_note += (
                f" In the All tab, set `reasoning_effort` to `{effort}` only if the installed backend "
                "schema and selected model support it. Review any other requested fields against that schema."
            )
    if chosen in _OPENROUTER_TASK_ENDPOINTS:
        native_note = ("Use the staged fields in a request to the listed OpenRouter endpoint. "
                       "Add the endpoint's task-specific input and authenticate separately.")
    if (text_candidate and (registered.id, surface, provider_id, chosen) ==
            ("cline", "desktop", "openrouter", "openrouter-chat")):
        native_note = (
            "Configure OpenRouter in Cline Desktop and select the staged model in the chat model picker. "
            "Use the session's Thinking level picker when the model supports reasoning."
        )
        thinking_labels = {"none": "None", "low": "Low", "medium": "Medium", "high": "High",
                           "xhigh": "Extra", "max": "Max"}
        if effort is not None and effort in thinking_labels:
            native_note += f" Requested Thinking level: {thinking_labels[effort]}."
            if effort == "max":
                native_note += (
                    " Select Max only if the installed release exposes it for this model. "
                    "Desktop 0.0.49 does not support Max."
                )
        elif effort is not None:
            native_note += " The requested effort has no matching label in the audited Desktop picker."
        if any(value is not None for value in (budget_tokens, reasoning_enabled, mode, context_size)):
            native_note += " Review the documented provider controls for the other requested settings."
    if batch_endpoint is not None:
        native_note = (
            "Put the staged endpoint and model fields before requests in the batch envelope. "
            "Add one or more entries with unique custom_id values and endpoint-specific request bodies. "
            "If present, merge the reasoning fragment into each intended request body. "
            "Supply authentication separately and review provider compatibility before submission."
        )
    codex_lmstudio = (text_candidate and (registered.id, surface, provider_id, chosen) ==
                      ("codex", "cli", "lm-studio", "openai-responses"))
    claude_lmstudio = (text_candidate and (registered.id, surface, provider_id, chosen) ==
                       ("claude-code", "cli", "lm-studio", "anthropic-messages"))
    copilot_byok = (
        (registered.id, surface) == ("copilot", "cli")
        and provider_id in {"openrouter", "lm-studio"}
        and chosen in {"openrouter-chat", "openai-chat", "openai-responses"}
    )
    copilot_app_byok = (
        (registered.id, surface) == ("copilot", "desktop")
        and provider_id in {"openrouter", "lm-studio"}
        and chosen in {"openrouter-chat", "openai-chat"}
    )
    if copilot_app_byok and text_candidate and not model_id.endswith(":batch"):
        provider_label = "LM Studio" if provider_id == "lm-studio" else "OpenAI-compatible HTTP (OpenRouter)"
        native_note = (
            f"Open Settings > Model providers > Add provider and choose {provider_label}. "
            f"Use the documented provider base URL `{api_base}` if requested. Add any required credential "
            f"in the app, then locate `{model_id}` in the session model picker. "
            "Choose a reasoning effort below the prompt when the selected model offers one. "
            "Catalog-validated API request fields are separate from the app picker."
        )
    hermes_config = None
    if ((registered.id, surface) == ("hermes", "cli") and budget_tokens is None and mode is None
            and (reasoning_enabled is None or (provider_id == "lm-studio" and reasoning_enabled is False))):
        hermes_config = _hermes_model_config(checked, api_base, effort, context_size)
    codex_effort = checked["reasoning_payload"].get("reasoning", {}).get("effort") if codex_lmstudio else None
    if (codex_lmstudio and all(value is None for value in (budget_tokens, mode, context_size))
            and (reasoning_enabled is None or (reasoning_enabled is False and codex_effort == "none"))):
        profile = ['# Staged Codex CLI profile for LM Studio.',
                   f'model = {json.dumps(model_id)}', 'model_provider = "lmstudio"']
        if codex_effort is not None:
            profile.append(f'model_reasoning_effort = {json.dumps(codex_effort)}')
        artifacts.append(RenderedArtifact(PurePosixPath(f"{prefix}/weaver-lmstudio.config.toml"),
                                          ("\n".join(profile) + "\n").encode(), "model-config"))
        native = True
        native_note = ("Review the profile and place it as a new named file in your Codex home. "
                       "Use codex --profile weaver-lmstudio with an already running, approved local LM Studio server. "
                       "Preserve existing profiles and review higher-priority settings and process environment overrides. "
                       "The profile selects Codex's built-in lmstudio provider without configuring authentication "
                       "or starting a server.")
    elif claude_lmstudio and all(value is None for value in (effort, budget_tokens, reasoning_enabled, mode, context_size)):
        fragment = {"model": model_id, "env": {
            "ANTHROPIC_BASE_URL": api_base,
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        }}
        artifacts.append(_json_artifact(f"{prefix}/lmstudio.settings.json", fragment, "model-config"))
        native = True
        native_note = (
            "Review the staged file and pass it to Claude Code with --settings for a single session "
            "against an already running, approved LM Studio server. Set ANTHROPIC_AUTH_TOKEN in the "
            "launch environment to your LM Studio token when server authentication is required. "
            "For a server without authentication, LM Studio documents the placeholder lmstudio. "
            "Check inherited environment settings, including ANTHROPIC_MODEL, ANTHROPIC_API_KEY, "
            "and provider flags, before launching. Managed policy still applies."
        )
    elif copilot_byok and text_candidate and not model_id.endswith(":batch"):
        from .copilot_models import COPILOT_CLI_EFFORT_FLAGS, copilot_cli_provider_fragment

        parameters = checked["supported_parameters"]
        if parameters is not None and "tools" not in parameters:
            native_note = "The saved OpenRouter entry does not advertise tool calling, which Copilot CLI requires."
        elif any(value is not None for value in (budget_tokens, reasoning_enabled, mode)):
            native_note = "Copilot CLI provider staging supports named effort. Review budget, toggle and mode separately."
        elif effort is not None and effort not in COPILOT_CLI_EFFORT_FLAGS:
            native_note = "The requested effort has no documented Copilot CLI launch flag."
        elif effort is not None and provider_id == "lm-studio" and chosen != "openai-responses":
            native_note = "Use the LM Studio Responses route to stage a named effort for Copilot CLI."
        elif (effort is not None and parameters is not None
              and not {"reasoning", "reasoning_effort"}.intersection(parameters)):
            native_note = "The saved OpenRouter entry does not advertise a reasoning parameter."
        else:
            fragment, selector = copilot_cli_provider_fragment(
                provider_id, model_id, api_base, wire_api="responses" if chosen == "openai-responses" else "completions",
                efforts=(checked["reasoning_capability"]["efforts"]
                         if provider_id == "openrouter" or chosen == "openai-responses" else None),
                context_size=checked["context_size"],
                tool_calls=parameters is not None and "tools" in parameters,
            )
            artifacts.append(_json_artifact(f"{prefix}/{provider_id}.providers.json", fragment, "model-config"))
            command = f"copilot --model {selector}" + (f" --effort={effort}" if effort is not None else "")
            native = True
            native_note = (
                "Review the staged entries and merge them into your private providers.json, or point "
                "COPILOT_PROVIDERS_CONFIG at a private reviewed file. Preserve existing entries and resolve "
                f"conflicts by provider name and model ID. Select the staged model with `{command}`. "
                "Confirm streaming, tool calling and input/output token limits for the selected model. "
                "Capability metadata advertises available effort levels. The launch flag selects one. "
            )
            if provider_id == "openrouter":
                native_note += (
                    "Add your OpenRouter token as bearerToken to the provider entry in the private copy. "
                    "Copilot treats environment-variable references in JSON as literal strings."
                )
            else:
                native_note += (
                    "Use an independently running, approved LM Studio server on this host. "
                    "Add bearerToken in the private copy only if the server requires authentication."
                )
    elif hermes_config is not None:
        identifier = hermes_config["model"]["provider"]
        artifacts.append(_json_artifact(f"{prefix}/{identifier}.model.fragment.yaml", hermes_config, "model-config"))
        native = True
        native_note = "Review and merge the staged settings into your chosen Hermes profile's config.yaml. "
        if provider_id == "openrouter":
            native_note += "Authenticate through Hermes and review provider routing, fallbacks and effort limits."
        else:
            native_note += (
                "Review existing model authentication, LM_BASE_URL, auxiliary routes and fallbacks before use. "
                "Supply LM_API_KEY separately if your already running LM Studio server requires a token. "
                "Hermes may load a model when used. A loopback URL must resolve to the same host or network namespace "
                "as the server. A saved model maximum does not set the loaded context."
            )
    elif zed_fragment:
        zed_field = "subagent_model" if zed_subagent else "default_model"
        zed_model: dict[str, Any] = {"provider": "lmstudio" if zed_lmstudio else provider_id,
                                     "model": zed_model_id, **zed_google_fields}
        if not zed_google and effort is not None:
            zed_model["enable_thinking"] = effort != "none"
            if not (zed_openai and effort == "none"):
                zed_model["effort"] = effort
        elif not zed_google and reasoning_enabled is not None:
            zed_model["enable_thinking"] = reasoning_enabled
        if zed_speed is not None:
            zed_model["speed"] = zed_speed
        native = True
        if zed_profile is None:
            artifacts.append(_json_artifact(f"{prefix}/{zed_field}.fragment.json",
                                            {"agent": {zed_field: zed_model}}, "model-config"))
            native_note = f"Review and replace only the agent.{zed_field} object in Zed settings."
            if zed_subagent:
                native_note += " Zed falls back to the parent thread model if the configured subagent model is unavailable."
        else:
            zed_profile_settings = {
                "name": zed_profile,
                "tools": {tool: True for tool in ("find_path", "grep", "list_directory", "read_file")},
                "enable_all_context_servers": False,
                "context_servers": {},
                "default_model": zed_model,
            }
            artifacts.append(_json_artifact(f"{prefix}/agent-profile.fragment.json",
                                            {"agent": {"profiles": {zed_profile: zed_profile_settings}}}, "model-config"))
            native_note = (
                f"Review and add the entry `agent.profiles.{zed_profile}` under a fresh ID in Zed user settings. "
                "Select it in the native Agent Panel. The profile exposes local read and search tools only. "
                "Zed tool permissions still apply."
            )
        if zed_lmstudio:
            native_note += (
                " Use the exact model ID shown by Zed's LM Studio picker or its configured `GET /api/v0/models` "
                "server. Supply `LMSTUDIO_API_KEY` separately if the server requires authentication. "
                "The native provider on Zed 1.28.1 sends Chat Completions without a reasoning override; "
                "model thinking follows the LM Studio server and model defaults."
            )
        if zed_google_provider_model is not None:
            artifacts.append(_json_artifact(
                f"{prefix}/google-budget.fragment.json",
                {"language_models": {"google": {"available_models": [zed_google_provider_model]}}},
                "provider-config",
            ))
            native_note += (
                " Merge only the reviewed model entry from google-budget.fragment.json into "
                "language_models.google.available_models. A same-ID entry overrides the fetched model."
            )
        if zed_google and reasoning_enabled is False:
            native_note += (
                " Zed sends the lowest level on level-based models, a zero budget on Gemini 2.5 Flash and "
                "Flash-Lite, and no thinking override on Gemini 2.5 Pro."
            )
        if zed_speed is not None:
            native_note += (
                " Zed sends OpenAI priority processing for fast and leaves the API tier unset for standard."
                if provider_id == "openai" else
                " Zed sends the selected speed in the Anthropic Messages request."
            )
        native_note += (
            f" Authenticate through Zed's {provider_id} provider and confirm the exact model and effort in the native "
            "Zed Agent picker. The saved provider catalog cannot establish what the account-specific Zed picker exposes."
        )
    elif (text_candidate and (registered.id, surface, provider_id, chosen) ==
          ("cline", "cli", "lm-studio", "openai-chat")
          and checked["visible_in_snapshot"]
          and all(value is None for value in (budget_tokens, mode, context_size))
          and checked["reasoning_payload"].keys() <= {"reasoning_effort"}):
        thinking = checked["reasoning_payload"].get("reasoning_effort")
        if thinking in {None, "none", "low", "medium", "high", "xhigh"}:
            # The Cline 3.0.74 LM Studio provider omits an explicit disabled effort.
            cline_provider = "lmstudio" if thinking is None else "openai-compatible"
            argv = ["--provider", cline_provider, "--model", model_id, "--auto-approve", "false"]
            if thinking is not None:
                argv.extend(["--thinking", thinking])
            artifacts.append(_json_artifact(f"{prefix}/session-arguments.json", argv, "session-arguments"))
            native_note = (
                f"Configure Cline's {cline_provider} provider for the reviewed local LM Studio Chat Completions "
                f"base URL `{api_base}` and exact model ID. Use the staged arguments in Cline CLI "
                "and keep tool auto-approval off. Keep the model server on loopback during local testing."
            )
    elif (text_candidate and (registered.id, surface, provider_id, chosen) ==
          ("cline", "cli", "openrouter", "openrouter-chat")
          and effort in {None, "none", "low", "medium", "high", "xhigh"}
          and all(value is None for value in (budget_tokens, reasoning_enabled, mode, context_size))):
        argv = ["--provider", "openrouter", "--model", model_id, "--auto-approve", "false"]
        if effort is not None:
            argv.extend(["--thinking", effort])
        artifacts.append(_json_artifact(f"{prefix}/session-arguments.json", argv, "session-arguments"))
        native_note = (
            "Pass the staged arguments to Cline CLI after authenticating separately. "
            "They select the session model and optional thinking effort. "
            "Keep --auto-approve false to require approval for tool calls. "
            "Cline CLI 3.0.74 can auto-approve calls to enabled MCP servers without a matching per-tool entry. "
            "Review Cline's tool and delegation settings before submitting work."
        )
    elif (text_candidate and registered.id == "qwen-code" and surface in {"cli", "desktop", "vscode"}
            and chosen in {"openai-chat", "openai-responses"}
            and mode is None and effort not in {"none", "minimal"}
            and not router_responses_extra_controls
            and not (chosen == "openai-chat" and effort == "max") and provider["secret_env"] is not None):
        model: dict[str, Any] = {"id": model_id, "baseUrl": api_base, "envKey": provider["secret_env"],
                                 "wireApi": "responses" if chosen == "openai-responses" else "chat-completions"}
        if effort is not None or budget_tokens is not None or reasoning_enabled is not None:
            reasoning: Any = False if reasoning_enabled is False else {}
            if isinstance(reasoning, dict):
                if effort is not None:
                    reasoning["effort"] = effort
                elif reasoning_enabled is True and checked["reasoning_capability"].get("on_value") is not None:
                    reasoning["effort"] = checked["reasoning_capability"]["on_value"]
                if budget_tokens is not None:
                    reasoning["budget_tokens"] = budget_tokens
            model["generationConfig"] = {"reasoning": reasoning}
        fragment = {"modelProviders": {"openai": [model]}, "model": {"name": model_id},
                    "security": {"auth": {"selectedType": "openai"}}}
        artifacts.append(_json_artifact(f"{prefix}/settings.fragment.json", fragment, "model-config"))
        native = True
        native_note = "Merge the staged fragment into Qwen settings deliberately; modelProviders replaces its matching scope."
    elif (text_candidate and registered.id == "qwen-code" and surface in {"cli", "desktop", "vscode"}
          and (provider_id, chosen) in {("anthropic", "anthropic-messages"), ("google", "google-generate-content")}
          and provider["secret_env"] is not None):
        auth_type = "anthropic" if provider_id == "anthropic" else "gemini"
        qwen_base = api_base if auth_type == "anthropic" else api_base.removesuffix("/v1beta")
        qwen_model = model_id if auth_type == "anthropic" else model_id.removeprefix("models/")
        native_efforts = {None, "low", "medium", "high"}
        if auth_type == "anthropic" and model_id in {"claude-sonnet-4-6", "claude-opus-5"}:
            native_efforts.add("max")
        if auth_type == "anthropic" and model_id == "claude-opus-5":
            native_efforts.add("xhigh")
        if (qwen_model and "/" not in qwen_model and effort in native_efforts
                and all(value is None for value in (budget_tokens, reasoning_enabled, mode))):
            entry: dict[str, Any] = {"id": qwen_model, "baseUrl": qwen_base, "envKey": provider["secret_env"]}
            generation: dict[str, Any] = {}
            if effort is not None:
                generation["reasoning"] = {"effort": effort}
            if context_size is not None:
                generation["contextWindowSize"] = context_size
            if generation:
                entry["generationConfig"] = generation
            fragment = {"modelProviders": {auth_type: [entry]}, "model": {"name": qwen_model},
                        "security": {"auth": {"selectedType": auth_type}}}
            artifacts.append(_json_artifact(f"{prefix}/settings.fragment.json", fragment, "model-config"))
            native = True
            native_note = ("Merge the model entry into the selected Qwen settings scope while preserving other "
                           "modelProviders entries. Qwen replaces the provider section across scopes. "
                           "Review the model and its effective effort in the target client.")
        else:
            native_note = ("Review the requested model and controls in Qwen Code. This exporter stages named efforts "
                           "whose Qwen mapping is established. Budgets and toggles require a manual configuration check.")
    elif text_candidate and registered.id == "kimi-code" and surface == "cli" and chosen in {
            "openai-chat", "openai-responses", "anthropic-messages"} and budget_tokens is None and mode is None and effort not in {"none", "minimal"} and not router_responses_extra_controls and provider["secret_env"] is not None:
        provider_type = {"openai-chat": "openai", "openai-responses": "openai_responses", "anthropic-messages": "anthropic"}[chosen]
        def quote(value: object) -> str:
            return json.dumps(value, ensure_ascii=True)
        lines = ['# Staged fragment; supply the model context limit before use.', 'default_model = "weaver-model"', "",
                 '[providers.weaver]', f'type = {quote(provider_type)}', f'base_url = {quote(api_base)}',
                 f'api_key_env = {quote(provider["secret_env"])}', "", '[models.weaver-model]', 'provider = "weaver"',
                 f'model = {quote(model_id)}', '# max_context_size = <documented model limit; required>', ""]
        if checked["context_size"] is not None:
            lines[-2] = f'max_context_size = {checked["context_size"]}'
            native = True
        cap = checked["reasoning_capability"]
        if cap["efforts"] is not None:
            host_efforts = [level for level in cap["efforts"] if level in {"low", "medium", "high", "xhigh", "max"}]
            if host_efforts:
                lines += [f'support_efforts = {json.dumps(host_efforts)}']
        if cap.get("off_value") is not None:
            lines += [f'off_effort = {quote(cap["off_value"])}']
        if effort is not None or reasoning_enabled is not None:
            lines += ['[thinking]']
            if effort is not None:
                lines += [f'effort = {quote(effort)}']
            if reasoning_enabled is not None:
                lines += [f'enabled = {str(reasoning_enabled).lower()}']
        artifacts.append(RenderedArtifact(PurePosixPath(f"{prefix}/config.fragment.toml"),
                                          ("\n".join(lines) + "\n").encode(), "model-config"))
        # Required context metadata is intentionally not guessed; this remains a manual template.
        native_note = ("Merge the reviewed Kimi fragment into ~/.kimi-code/config.toml." if native else
                       "Kimi template requires max_context_size before use; supply --context-size or import documented context metadata.")
    selection = {"model": model_id, **checked["reasoning_payload"]}
    if endpoint_snapshot is not None:
        selection["provider"] = {"only": [openrouter_provider_tag], "require_parameters": True}
    if zed_speed is not None:
        if provider_id == "anthropic":
            selection["speed"] = zed_speed
        elif zed_speed == "fast":
            selection["service_tier"] = "priority"
    if perplexity_chain is not None:
        selection = {"models": perplexity_chain, **checked["reasoning_payload"]}
    selection.update(defaults, **request_overrides)
    if lmstudio_api_chat and context_size is not None:
        selection["context_length"] = context_size
    if batch_endpoint is not None:
        selection = {"endpoint": batch_endpoint, "model": checked["batch_model"]}
        if checked["reasoning_payload"]:
            artifacts.append(_json_artifact(f"{prefix}/batch-reasoning.fragment.json",
                                            checked["reasoning_payload"], "batch-reasoning"))
    artifacts.append(_json_artifact(f"{prefix}/model-selection.json", selection, "model-selection"))
    lines = [f"# Model setup: {registered.name}", "", "Staged offline handoff; no settings were installed or activated.", "",
             f"- Product/surface: `{registered.id}/{surface}`", f"- Provider: `{provider_id}`", f"- Model: `{model_id}`",
             f"- Protocol: `{chosen}`", f"- Configuration state: `{'available' if native else 'manual'}`",
             f"- Provider API base: `{api_base}`", f"- Credential environment reference: `{provider['secret_env']}`",
             f"- Visible in supplied snapshot: `{checked['visible_in_snapshot']}`", "- Inference/runtime verified: false",
             f"- Provider-declared architecture: `{json.dumps(architecture, ensure_ascii=True)}`",
             "- Language-agent compatibility: unknown",
             "- Catalog completeness and account availability: unknown", "", native_note, "",
             ("Keep the model-selection fragment as a provider API reference. "
              if air_ide_model or air_desktop_lmstudio else
              "Review the model-selection fragment against the selected API and merge only intended settings. ") +
             "Supply credentials through the product's documented local authentication flow. "
             "No API key is read or written by this generator. An explicit ID is not evidence of model access. "
             "Model selection does not install custom agents, Skills, MCP servers, or enable autonomous operation.", "",
             f"Documentation checked on: {provider['checked_on']}", "", "Sources:", ""]
    lines.extend(f"- {source}" for source in provider["sources"])
    if turn_update is not None:
        artifacts.append(_json_artifact(f"{prefix}/conversation-update.json", turn_update, "conversation-update"))
        history = "input" if chosen == "openai-responses" else "messages"
        lines.extend([
            "", f"Insert the item from `conversation-update.json` into `{history}` immediately before the user turn.",
            "Keep the request-level baseline and the item's position in subsequent requests. "
            "Keep updates separated by conversation items. Responses requires truncation other than `auto`.",
            f"- {_OPENROUTER_TURN_EFFORT_DOC}",
        ])
    if endpoint_snapshot is not None:
        lines.extend(["", f"Provider tag: `{openrouter_provider_tag}`.",
                      f"Saved endpoint listing SHA-256: `{endpoint_snapshot['sha256']}`.",
                      "The gateway will restrict routing to matching providers that support the supplied parameters. "
                      "OpenRouter can match variants and regions for a base or partial tag. "
                      "Current account access and effective reasoning remain unverified.", "",
                      "- https://openrouter.ai/docs/api/api-reference/endpoints/list-all-endpoints-for-a-model",
                      "- https://openrouter.ai/docs/guides/routing/provider-selection"])
    if lmstudio_api_chat and context_size is not None:
        lines.extend(["", f"Merge the staged context length of `{context_size}` into `POST /api/v1/chat`. "
                      "Use a downloaded model key if the request needs a different context from a loaded instance."])
    if instance := checked.get("lm_studio_loaded_instance"):
        lines.extend(["", f"Saved LM Studio loaded instance of `{instance['model_key']}`.",
                      f"Captured instance context length: `{instance['context_length']}`.",
                      "Refresh the snapshot if the instance is unloaded, reloaded, or renamed."])
    if air_ide_model:
        lines.extend([f"- {AIR_IDE_AUTHORIZATION}", f"- {AIR_IDE_SESSION_SETTINGS}"])
    if air_desktop_lmstudio:
        lines.append("- https://blog.jetbrains.com/air/2026/07/"
                     "what-s-new-air-gets-more-agents-local-models-and-java-kotlin-code-intelligence/")
    if canvas_model:
        lines.append("- https://docs.openhands.dev/openhands/usage/agent-canvas/llm-profiles")
    if codex_lmstudio:
        lines.extend(["- https://lmstudio.ai/docs/integrations/codex",
                      "- https://learn.chatgpt.com/docs/config-file/config-advanced#profiles"])
    if claude_lmstudio:
        lines.extend(["- https://lmstudio.ai/docs/integrations/claude-code",
                      "- https://code.claude.com/docs/en/cli-reference",
                      "- https://code.claude.com/docs/en/settings"])
    if copilot_byok:
        from .copilot_models import COPILOT_PROVIDERS_DOC

        lines.extend([f"- {COPILOT_PROVIDERS_DOC}",
                      "- https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-llm-models"])
        if provider_id == "lm-studio":
            lines.append("- https://lmstudio.ai/docs/developer/openai-compat/responses" if chosen == "openai-responses"
                         else "- https://lmstudio.ai/docs/developer/openai-compat/tools")
    if copilot_app_byok:
        lines.extend(["- https://docs.github.com/en/copilot/how-tos/github-copilot-app/add-llm-models",
                      "- https://docs.github.com/en/copilot/how-tos/github-copilot-app/agent-sessions"])
    if (registered.id, surface) == ("hermes", "cli") and provider_id in {"lm-studio", "openrouter"}:
        lines.extend(["- https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models",
                      "- https://hermes-agent.nousresearch.com/docs/user-guide/configuration#reasoning-effort"])
        if provider_id == "lm-studio":
            lines.append("- https://hermes-agent.nousresearch.com/docs/integrations/providers#lm-studio--desktop-app-with-local-models")
    if chosen in _LM_STUDIO_NONCHAT:
        model_type, endpoint = _LM_STUDIO_NONCHAT[chosen]
        lines.extend(["", f"Endpoint: `POST {api_base}{endpoint}`.",
                      f"Add task-specific input using the documented {model_type} request schema. "
                      "The selection fragment contains only the model field. Confirm the model is available "
                      "on the target server before sending a request."])
    if chosen in _OPENROUTER_TASK_ENDPOINTS:
        endpoint = _OPENROUTER_TASK_ENDPOINTS[chosen]
        lines.extend(["", f"Endpoint: `POST {api_base.removesuffix('/api/v1')}{endpoint}`.",
                      "The saved listing advertises this route for the selected model."])
    if registered.id == "qwen-code":
        lines.append("- https://qwenlm.github.io/qwen-code-docs/en/users/configuration/model-providers/")
        if surface == "vscode":
            lines.append("- https://github.com/QwenLM/qwen-code/blob/ecf01e9ece119ee7676898f95dadcac2bdc6d6cc/packages/cli/src/acp-integration/acpAgent.ts#L5970-L6012")
        if surface == "desktop":
            lines.append("- https://github.com/QwenLM/qwen-code/releases/tag/desktop-v0.27.3")
        if provider_id == "anthropic":
            lines.append("- https://github.com/QwenLM/qwen-code/blob/fcfddc3700c58ab1417feb2a780b5a9b74dbbe3d/packages/core/src/core/anthropic-reasoning.ts#L78-L116")
        elif provider_id == "google":
            lines.append("- https://github.com/QwenLM/qwen-code/blob/fcfddc3700c58ab1417feb2a780b5a9b74dbbe3d/packages/core/src/core/llm-content-generator/llm-content-generator.ts#L264-L339")
    if (registered.id, surface) == ("cline", "cli"):
        lines.append("- https://docs.cline.bot/cli/cli-reference")
        if provider_id == "lm-studio":
            lines.append("- https://docs.cline.bot/running-models-locally/overview")
    if (registered.id, surface) == ("cline", "desktop"):
        lines.append("- https://docs.cline.bot/usage/cline-desktop")
    if perplexity_agent:
        lines.extend(["", f"Merge these fields into a reviewed `POST {perplexity_endpoint}` request "
                      "and supply the input separately.",
                      "Confirm access to the selected models in the target project."])
        if perplexity_max_output_tokens is not None:
            lines.append("The output token setting applies to the run.")
        lines.extend(defaults_notes)
        if perplexity_chain is not None:
            lines.extend(["The `models` array gives the API its attempt order. Keep the request's model selection in that array.",
                          "- https://docs.perplexity.ai/docs/agent-api/model-fallback"])
        if effort == "max" and not perplexity_openai:
            lines.extend(["", "Perplexity Python SDK 0.46.1 rejects `max` in the typed `reasoning` argument. "
                          "With this SDK, pass `extra_body={\"reasoning\": {\"effort\": \"max\"}}` to "
                          "`client.responses.create` and omit its typed `reasoning` argument. "
                          "The staged JSON already has the REST request shape.",
                          "- https://github.com/perplexityai/perplexity-py/tree/v0.46.1#making-customundocumented-requests"])
    if provider_id == "perplexity-orchestrator":
        lines.extend(["", "Perplexity lists Orchestrator as private preview. Confirm access and the model ID with "
                      "an authenticated `GET /models` or `GET /models/{model}` and pass the full model ID. "
                      "These catalog routes report visibility and prices. Effective per-model reasoning is unverified."])
        if checked["catalog_completeness"] == "partial":
            lines.append("The saved response provides partial catalog evidence.")
        lines.extend({
            "openai-chat": ["Pass the Perplexity key to the OpenAI SDK's `api_key` argument. "
                            "Add `messages` to the staged fields for `client.chat.completions.create`."],
            "openai-responses": ["Pass the Perplexity key to the OpenAI SDK's `api_key` argument for "
                                 "`client.responses.create`. Orchestrator Responses is stateless. "
                                 "Send the full conversation in `input` and omit `previous_response_id`. "
                                 "Leave `store` unset or set it to `false`."],
            "anthropic-messages": ["Pass the Perplexity key to the Anthropic SDK's `api_key` argument. "
                                   "Add `messages` and `max_tokens` for `client.messages.create`."],
        }[chosen])
    if zed_fragment:
        lines.extend(["- https://zed.dev/docs/ai/agent-settings#subagent-models",
                      f"- {_ZED_PROVIDER_SOURCES[provider_id]}"])
        if zed_lmstudio:
            lines.extend(["- https://zed.dev/docs/ai/use-a-local-model#lm-studio",
                          "- https://lmstudio.ai/docs/developer/rest/endpoints"])
        if zed_speed is not None:
            lines.append("- https://github.com/zed-industries/zed/blob/v1.28.1/crates/" + (
                "open_ai/src/open_ai.rs#L450-L478" if provider_id == "openai" else
                "anthropic/src/anthropic.rs#L19-L26"
            ))
        if zed_profile is not None:
            lines.append("- https://zed.dev/docs/ai/agent-profiles")
    if registered.id == "kimi-code":
        lines.append("- https://moonshotai.github.io/kimi-code/en/configuration/providers.html")
    if chosen == "anthropic-messages" and provider_id in {"lm-studio", "perplexity-orchestrator"}:
        lines.extend(["", "The Anthropic SDK appends `/v1/messages` to this protocol base; "
                      "do not append `/v1` again in its base URL."])
        lines.append("- https://lmstudio.ai/docs/developer/anthropic-compat" if provider_id == "lm-studio" else
                     "- https://docs.perplexity.ai/api-reference/gateway-messages-post")
    artifacts.insert(0, RenderedArtifact(PurePosixPath(f"{prefix}/model-setup.md"), ("\n".join(lines) + "\n").encode(), "model-setup"))
    return artifacts
