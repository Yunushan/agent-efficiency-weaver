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
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from urllib.parse import parse_qsl, urlsplit

from .artifacts import RenderedArtifact
from .config import WeaverError, load_json, safe_https_url
from .integrations import IntegrationSpec, get_integration, load_integrations

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MODEL = re.compile(r"^~?[A-Za-z0-9][A-Za-z0-9._:+@/-]*$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_PROTOCOLS = {
    "openai-chat", "openai-responses", "anthropic-messages", "google-generate-content",
    "lmstudio-chat", "perplexity-agent", "openrouter-chat",
}
_SECRET_KEYS = {"api_key", "apikey", "authorization", "credentials", "credential", "secret", "access_token",
                "refresh_token", "password", "headers", "apiKey", "token"}
_SECRET = re.compile(r"(?:sk-(?:or-|ant-|proj-)?[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{15,}|gh[pousr]_[A-Za-z0-9_]{12,}|Bearer\s+[A-Za-z0-9._~-]{12,})",
                     re.IGNORECASE)
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


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


def _scan(value: object, depth: int = 0) -> None:
    """Reject secrets/non-finite data even in fields intentionally not retained."""
    if depth > 40:
        _fail("Model metadata is nested too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.casefold().replace("-", "_") in _SECRET_KEYS:
                _fail("Credentials are not accepted in model metadata")
            _scan(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _scan(item, depth + 1)
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
                           allowed={"off", "on", "low", "medium", "high"})
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
            if "pricing" in entry:
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
    if protocol == "anthropic-messages":
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
                mode: str | None = None, context_size: int | None = None) -> dict[str, Any]:
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
    cap = _reasoning(None)
    evidence: dict[str, Any] = {"origin": "unknown", "sources": []}
    for known in provider["known_models"]:
        if known["id"] == identifier:
            cap = _reasoning(known["reasoning"])
            evidence = {"origin": "documentation", "sources": known["sources"], "checked_on": provider["checked_on"]}
    visible: bool | None = None
    provenance: dict[str, Any] | None = None
    if snapshot is not None:
        if not isinstance(snapshot, Mapping) or snapshot.get("provider_id") != provider_id:
            _fail("Snapshot provider does not match requested provider")
        _scan(dict(snapshot))
        entries = snapshot.get("models")
        if not isinstance(entries, list):
            _fail("Expected a normalized snapshot from read_model_snapshot")
        seen: set[str] = set()
        visible = False
        for entry in entries:
            if not isinstance(entry, dict):
                _fail("Invalid normalized snapshot")
            entry_id = _model_id(entry.get("id"))
            if entry_id in seen:
                _fail("Duplicate model id in snapshot")
            seen.add(entry_id)
            if entry_id == identifier:
                visible = True
                if context_size is None and "context_length" in entry:
                    context = entry["context_length"]
                    if type(context) is not int or context < 1:
                        _fail("Invalid model context size in snapshot")
                    context_size = context
                supplied = entry.get("reasoning")
                if isinstance(supplied, dict) and supplied.get("state") != "unknown":
                    cap = _reasoning({k: v for k, v in supplied.items() if k != "state"})
                    evidence = {"origin": "caller-supplied-snapshot", "sources": []}
        if snapshot.get("provenance") is not None:
            provenance = _snapshot_provenance(snapshot["provenance"], normalized=True)
    payload = _wire(chosen, cap, effort=effort, budget_tokens=budget_tokens, reasoning_enabled=reasoning_enabled, mode=mode)
    return {"provider_id": provider_id, "integration_id": provider["integration_id"], "surface": provider["surface"],
            "model_id": identifier, "protocol": chosen, "accepted_explicit_id": True, "visible_in_snapshot": visible,
            "inference_tested": False, "runtime_verified": False, "availability": "unknown",
            "reasoning_capability": cap, "reasoning_evidence": evidence, "reasoning_payload": payload,
            "snapshot_provenance": provenance, "catalog_completeness": "unknown", "network_used": False,
            "credentials_read": False, "context_size": context_size}


def _json_artifact(path: str, value: object, kind: str) -> RenderedArtifact:
    return RenderedArtifact(PurePosixPath(path), (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode(), kind)


def render_model_setup(spec: IntegrationSpec, surface: str, *, root: Path, provider_id: str, model_id: str,
                       snapshot: Mapping[str, Any] | None = None, effort: str | None = None,
                       budget_tokens: int | None = None, reasoning_enabled: bool | None = None,
                       protocol: str | None = None, mode: str | None = None,
                       context_size: int | None = None) -> list[RenderedArtifact]:
    """Stage a reviewed fragment only for documented routes; otherwise give a manual handoff."""
    registered = get_integration(spec.id, root)
    registered.surface(surface)
    checked = check_model(root, provider_id, model_id, snapshot, effort=effort, budget_tokens=budget_tokens,
                          reasoning_enabled=reasoning_enabled, protocol=protocol, mode=mode, context_size=context_size)
    provider = load_model_providers(root)[provider_id]
    prefix = f"{registered.id}/model"
    chosen = checked["protocol"]
    api_base = provider["base_url"]
    if provider_id == "lm-studio" and chosen != "lmstudio-chat":
        api_base += "/v1"
    native = False
    artifacts: list[RenderedArtifact] = []
    native_note = "No native model configuration exporter is documented for this product/surface/provider route."
    if (registered.id == "qwen-code" and surface == "cli" and chosen in {"openai-chat", "openai-responses"}
            and mode is None and effort not in {"none", "minimal"}
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
    elif registered.id == "kimi-code" and surface == "cli" and chosen in {
            "openai-chat", "openai-responses", "anthropic-messages"} and budget_tokens is None and mode is None and effort not in {"none", "minimal"} and provider["secret_env"] is not None:
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
    artifacts.append(_json_artifact(f"{prefix}/model-selection.json", selection, "model-selection"))
    lines = [f"# Model setup: {registered.name}", "", "Staged offline handoff; no settings were installed or activated.", "",
             f"- Product/surface: `{registered.id}/{surface}`", f"- Provider: `{provider_id}`", f"- Model: `{model_id}`",
             f"- Protocol: `{chosen}`", f"- Configuration state: `{'available' if native else 'manual'}`",
             f"- Provider API base: `{api_base}`", f"- Credential environment reference: `{provider['secret_env']}`",
             f"- Visible in supplied snapshot: `{checked['visible_in_snapshot']}`", "- Inference/runtime verified: false",
             "- Catalog completeness and account availability: unknown", "", native_note, "",
             "Review the model-selection fragment against the selected API and merge only intended settings. "
             "Supply credentials through the product's documented local authentication flow. "
             "No API key is read or written by this generator. An explicit ID is not evidence of model access. "
             "Model selection does not install custom agents, Skills, MCP servers, or enable autonomous operation.", "",
             f"Documentation checked on: {provider['checked_on']}", "", "Sources:", ""]
    lines.extend(f"- {source}" for source in provider["sources"])
    if registered.id == "qwen-code":
        lines.append("- https://qwenlm.github.io/qwen-code-docs/en/users/configuration/model-providers/")
    if registered.id == "kimi-code":
        lines.append("- https://moonshotai.github.io/kimi-code/en/configuration/providers.html")
    artifacts.insert(0, RenderedArtifact(PurePosixPath(f"{prefix}/model-setup.md"), ("\n".join(lines) + "\n").encode(), "model-setup"))
    return artifacts
