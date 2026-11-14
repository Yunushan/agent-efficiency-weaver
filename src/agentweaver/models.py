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
_SECRET = re.compile(r"(?:sk-(?:or-|ant-|proj-)?[A-Za-z0-9_-]{8,}|crsr_[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{15,}|gh[pousr]_[A-Za-z0-9_]{12,}|Bearer\s+[A-Za-z0-9._~-]{12,})",
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
        if discovery["format"] not in {"openrouter", "openai", "anthropic", "google", "lmstudio"}:
            _fail("Unsupported snapshot format")
        known = entry["known_models"]
        if not isinstance(known, list):
            _fail("Invalid documented models")
        seen: set[str] = set()
        for model in known:
            if not isinstance(model, dict) or set(model) != {"id", "reasoning", "sources"}:
                _fail("Invalid documented model")
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


def _snapshot(raw: dict[str, Any], provider_id: str, provider: dict[str, Any], digest: str) -> dict[str, Any]:
    _scan(raw)
    if raw.get("provider_id", provider_id) != provider_id:
        _fail("Snapshot provider does not match requested provider")
    format_name = provider["discovery"]["format"]
    pages = raw.get("pages", [raw])
    if not isinstance(pages, list) or not pages:
        _fail("Snapshot pages must be a non-empty list")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_metadata: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            _fail("Invalid model snapshot page")
        field = "models" if format_name in {"google", "lmstudio"} else "data"
        entries = page.get(field)
        if not isinstance(entries, list):
            _fail(f"Snapshot requires {field} array")
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
    incomplete = any(p.get("has_more") is True or p.get("nextPageToken") or p.get("links", {}).get("next") for p in page_metadata)
    return {"schema_version": 1, "provider_id": provider_id, "format": format_name,
            "models": models, "model_count": len(models), "pagination": {"pages": page_metadata,
            "page_count": len(pages), "has_unresolved_pages": bool(incomplete)},
            "completeness": "partial" if incomplete else "unknown", "provenance": {
                "origin": "caller-supplied", "sha256": digest, "discovery_scope": provider["discovery"]["scope"],
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
    lmstudio_options: list[str] | None = None
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
            if entry_id == expected_id:
                visible = True
                architecture = declared_architecture
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
                if "context_length" in entry:
                    context = entry["context_length"]
                    if type(context) is not int or context < 0:
                        _fail("Invalid model context size in snapshot")
                    # A provider-declared zero does not establish a usable limit.
                    if context_size is None and context > 0:
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
    if lmstudio_switch and lmstudio_options is not None and reasoning_enabled is not None:
        if type(reasoning_enabled) is not bool:
            _fail("Reasoning toggle must be boolean")
        requested = "on" if reasoning_enabled else "off"
        if requested not in lmstudio_options or any(value is not None for value in (effort, budget_tokens, mode)):
            _fail("LM Studio reasoning switch is unsupported or conflicts with another reasoning control")
        payload = {
            "lmstudio-chat": {"reasoning": requested},
            "anthropic-messages": {"thinking": {"type": "adaptive" if reasoning_enabled else "disabled"}},
            "openai-responses": {"reasoning": {"effort": "none"}},
            "openai-chat": {"reasoning_effort": "none"},
        }[chosen]
    else:
        if chosen == "lmstudio-chat" and effort is not None and (
                effort not in {"low", "medium", "high"} or
                (lmstudio_options is not None and effort not in lmstudio_options)):
            _fail("Unsupported LM Studio native reasoning effort")
        reasoning_protocol = _OPENROUTER_BATCH_REASONING.get(batch_endpoint, chosen) if batch_endpoint else chosen
        if provider_id == "openrouter" and reasoning_protocol == "openai-responses":
            reasoning_protocol = "openrouter-chat"
        payload = _wire(reasoning_protocol, cap, effort=effort, budget_tokens=budget_tokens,
                        reasoning_enabled=reasoning_enabled, mode=mode)
    return {"provider_id": provider_id, "integration_id": provider["integration_id"], "surface": provider["surface"],
            "model_id": identifier, "protocol": chosen, "accepted_explicit_id": True, "visible_in_snapshot": visible,
            "inference_tested": False, "runtime_verified": False, "availability": "unknown",
            "reasoning_capability": cap, "reasoning_evidence": evidence, "reasoning_payload": payload,
            "snapshot_provenance": provenance, "catalog_completeness": "unknown", "network_used": False,
            "credentials_read": False, "context_size": context_size, "architecture": architecture,
            "advertised_endpoints": endpoints, "supported_parameters": supported_parameters,
            "batch_model": batch_model if batch else None, "batch_endpoint": batch_endpoint,
            "language_agent_compatibility": "unknown", **openrouter_metadata}


def _json_artifact(path: str, value: object, kind: str) -> RenderedArtifact:
    return RenderedArtifact(PurePosixPath(path), (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode(), kind)


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


def render_kiro_cli_model(model: str, effort: str | None, *, persistent: bool = False) -> list[RenderedArtifact]:
    """Stage Kiro service model settings using the documented V3 controls."""
    identifier = _model_id(model)
    if "/" in identifier or identifier.startswith(("~", "-")):
        _fail("Kiro requires a Kiro model service ID")
    claude_efforts = {"low", "medium", "high", "max"}
    extended_claude = {"claude-opus-5", "claude-opus-4.8", "claude-opus-4.7", "claude-sonnet-5"}
    gpt_models = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
    if identifier in gpt_models or identifier in extended_claude:
        allowed = claude_efforts | {"xhigh"}
    elif identifier == "claude-sonnet-5.5":
        # Use the intersection of the effort table and the model's specific note.
        allowed = {"low", "medium", "high"}
    elif identifier in {"claude-opus-4.6", "claude-sonnet-4.6"}:
        allowed = claude_efforts
    else:
        allowed = set()
    if persistent:
        if identifier in gpt_models:
            allowed.add("none")
        if effort is None:
            _fail("Kiro per-model defaults require an explicit reasoning effort")
    if effort is not None and effort not in allowed:
        _fail("Requested effort is unsupported or unknown for this Kiro CLI model")

    if persistent:
        field = "reasoning" if identifier in gpt_models else "output_config"
        fragment = {"chat.modelDefaults": {identifier: {field: {"effort": effort}}}}
        output = _json_artifact("kiro/model/cli.fragment.json", fragment, "model-config")
        instructions = (
            "Review and merge only the selected model entry under chat.modelDefaults in "
            ".kiro/settings/cli.json for this workspace or ~/.kiro/settings/cli.json for this user. "
            "Preserve other model settings and entries. The selected model's effort can be overridden by the session."
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


def render_model_setup(spec: IntegrationSpec, surface: str, *, root: Path, provider_id: str, model_id: str,
                       snapshot: Mapping[str, Any] | None = None, effort: str | None = None,
                       budget_tokens: int | None = None, reasoning_enabled: bool | None = None,
                       protocol: str | None = None, mode: str | None = None,
                       context_size: int | None = None, batch_endpoint: str | None = None,
                       zed_subagent: bool = False) -> list[RenderedArtifact]:
    """Stage a reviewed fragment only for documented routes; otherwise give a manual handoff."""
    registered = get_integration(spec.id, root)
    registered.surface(surface)
    if protocol in _LM_STUDIO_NONCHAT and (registered.id, surface) != ("lm-studio", "api"):
        _fail("Non-chat LM Studio selections require the LM Studio API surface")
    if protocol in _OPENROUTER_TASK_ENDPOINTS and (registered.id, surface) != ("openrouter", "gateway"):
        _fail("OpenRouter task endpoint selections require the OpenRouter gateway surface")
    if protocol is None and provider_id == "lm-studio" and (registered.id, surface) in {
        ("copilot", "cli"), ("copilot", "desktop"), ("openhands", "canvas")
    }:
        protocol = "openai-chat"
    checked = check_model(root, provider_id, model_id, snapshot, effort=effort, budget_tokens=budget_tokens,
                          reasoning_enabled=reasoning_enabled, protocol=protocol, mode=mode, context_size=context_size,
                          batch_endpoint=batch_endpoint)
    provider = load_model_providers(root)[provider_id]
    prefix = f"{registered.id}/model"
    chosen = checked["protocol"]
    router_responses_extra_controls = (provider_id, chosen) == ("openrouter", "openai-responses") and (
        budget_tokens is not None or reasoning_enabled is not None
    )
    api_base = provider["base_url"]
    if provider_id == "lm-studio" and (chosen in {"openai-chat", "openai-responses"} or chosen in _LM_STUDIO_NONCHAT):
        api_base += "/v1"
    elif chosen == "anthropic-messages":
        # Anthropic SDK clients append /v1/messages to their configured base URL.
        api_base = api_base.removesuffix("/v1")
    native = False
    artifacts: list[RenderedArtifact] = []
    native_note = "No native model configuration exporter is documented for this product/surface/provider route."
    architecture = checked["architecture"]
    text_candidate = architecture is None or "output_modalities" not in architecture or "text" in architecture["output_modalities"]
    zed_openrouter = (registered.id, surface, provider_id, chosen) == ("zed", "ide", "openrouter", "openrouter-chat")
    zed_fragment = (
        zed_openrouter and text_candidate and not model_id.endswith(":batch")
        and all(value is None for value in (budget_tokens, mode, context_size))
    )
    zed_parameters = checked["supported_parameters"]
    if zed_openrouter and (effort is not None or reasoning_enabled is not None) and (
        zed_parameters is not None and "reasoning" not in zed_parameters
    ):
        _fail("The saved OpenRouter model does not advertise the reasoning parameter used by Zed")
    if zed_subagent and not zed_fragment:
        _fail("Zed subagent settings require a Zed IDE OpenRouter chat model without budget, mode or context overrides")
    if not text_candidate:
        native_note = ("The supplied architecture does not declare text output. Use a task-specific manual handoff; "
                       "language-agent compatibility is unknown.")
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
        zed_model: dict[str, Any] = {"provider": "openrouter", "model": model_id}
        if effort is not None:
            zed_model.update(enable_thinking=effort != "none", effort=effort)
        elif reasoning_enabled is not None:
            zed_model["enable_thinking"] = reasoning_enabled
        artifacts.append(_json_artifact(f"{prefix}/{zed_field}.fragment.json",
                                        {"agent": {zed_field: zed_model}}, "model-config"))
        native = True
        native_note = (
            f"Review and replace only the agent.{zed_field} object in Zed settings. Authenticate through Zed's OpenRouter "
            "provider and confirm the exact model and effort in the native Zed Agent picker. "
            "The saved provider catalog cannot establish what the account-specific Zed picker exposes."
        )
        if zed_subagent:
            native_note += " Zed falls back to the parent thread model if the configured subagent model is unavailable."
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
             "Review the model-selection fragment against the selected API and merge only intended settings. "
             "Supply credentials through the product's documented local authentication flow. "
             "No API key is read or written by this generator. An explicit ID is not evidence of model access. "
             "Model selection does not install custom agents, Skills, MCP servers, or enable autonomous operation.", "",
             f"Documentation checked on: {provider['checked_on']}", "", "Sources:", ""]
    lines.extend(f"- {source}" for source in provider["sources"])
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
    if (registered.id, surface) == ("cline", "desktop"):
        lines.append("- https://docs.cline.bot/usage/cline-desktop")
    if zed_fragment:
        lines.extend(["- https://zed.dev/docs/ai/agent-settings#subagent-models",
                      "- https://github.com/zed-industries/zed/blob/v1.28.1/crates/language_models/src/provider/open_router.rs"])
    if registered.id == "kimi-code":
        lines.append("- https://moonshotai.github.io/kimi-code/en/configuration/providers.html")
    if chosen == "anthropic-messages" and provider_id in {"lm-studio", "perplexity-orchestrator"}:
        lines.extend(["", "The Anthropic SDK appends `/v1/messages` to this protocol base; "
                      "do not append `/v1` again in its base URL."])
        lines.append("- https://lmstudio.ai/docs/developer/anthropic-compat" if provider_id == "lm-studio" else
                     "- https://docs.perplexity.ai/api-reference/gateway-messages-post")
    artifacts.insert(0, RenderedArtifact(PurePosixPath(f"{prefix}/model-setup.md"), ("\n".join(lines) + "\n").encode(), "model-setup"))
    return artifacts
