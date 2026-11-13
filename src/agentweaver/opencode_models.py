"""Validate model selections against saved OpenCode V2 and Kilo model lists."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import WeaverError
from .models import _load_bounded_snapshot, _model_id, _read_snapshot_json, _scan, _text


def _variant_id(value: object, host: str) -> str:
    variant = _text(value, f"{host} variant ID")
    if not variant.strip() or "#" in variant:
        raise WeaverError(f"Invalid {host} variant ID")
    return variant


def project_opencode_catalog(path: Path) -> dict[str, Any]:
    """Keep only selectable identity and variant fields from an OpenCode V2 response."""
    payload, _ = _read_snapshot_json(path, "OpenCode model catalog")
    if not isinstance(payload, dict) or not isinstance(payload.get("location"), dict) or not isinstance(
        payload.get("data"), list
    ):
        raise WeaverError("Expected a saved OpenCode GET /api/model response")
    rows = []
    seen: set[tuple[str, str]] = set()
    for entry in payload["data"]:
        if not isinstance(entry, dict) or type(entry.get("enabled")) is not bool:
            raise WeaverError("Invalid OpenCode model entry")
        provider = _model_id(entry.get("providerID"))
        alias = _model_id(entry.get("id"))
        if "/" in provider or (provider, alias) in seen:
            raise WeaverError("Invalid or duplicate OpenCode provider and model entry")
        seen.add((provider, alias))
        variants = entry.get("variants")
        if not isinstance(variants, list) or any(not isinstance(variant, dict) for variant in variants):
            raise WeaverError("Invalid OpenCode model variants")
        variant_ids = [_variant_id(variant.get("id"), "OpenCode") for variant in variants]
        if len(set(variant_ids)) != len(variant_ids):
            raise WeaverError("Duplicate OpenCode model variant")
        rows.append({
            "providerID": provider,
            "id": alias,
            "modelID": _model_id(entry.get("modelID")),
            "enabled": entry["enabled"],
            "type": _model_id(entry.get("type", "chat")),
            "variants": [{"id": identifier} for identifier in variant_ids],
        })
    result = {"location": {}, "data": rows}
    _scan(result)
    return result


def validate_opencode_selection(path: Path, model: str, variant: str | None) -> tuple[str, str]:
    """Return a validated V2 selector and the catalog response's SHA-256."""
    return _validate_selection(path, model, variant, kilo=False)


def validate_kilo_selection(path: Path, model: str, variant: str | None) -> tuple[str, str]:
    """Check a Kilo model/variant pair and return the unchanged model selector and hash."""
    return _validate_selection(path, model, variant, kilo=True)


def _validate_selection(path: Path, model: str, variant: str | None, *, kilo: bool) -> tuple[str, str]:
    host = "Kilo" if kilo else "OpenCode"
    provider, separator, alias = model.partition("/")
    if not separator:
        raise WeaverError(f"A {host} selector requires provider/model")
    _model_id(provider)
    _model_id(alias)
    selected_variant = _variant_id(variant, host) if variant is not None else None
    reply, digest = _load_bounded_snapshot(path, f"{host} model catalog", allow_empty_headers=kilo)
    if not isinstance(reply, dict) or not isinstance(reply.get("location"), dict) or not isinstance(reply.get("data"), list):
        raise WeaverError(f"Expected a saved {host} GET /api/model response")
    seen: set[tuple[str, str]] = set()
    matched = False
    for row in reply["data"]:
        if not isinstance(row, dict) or type(row.get("enabled")) is not bool:
            raise WeaverError(f"Invalid {host} model entry")
        provider_id = _model_id(row.get("providerID"))
        entry_id = _model_id(row.get("id"))
        if "/" in provider_id:
            raise WeaverError(f"Invalid {host} provider ID")
        key = (provider_id, entry_id)
        if key in seen:
            raise WeaverError(f"Duplicate {host} provider and model entry")
        seen.add(key)
        if key != (provider, alias):
            continue
        if kilo:
            capabilities = row.get("capabilities")
            output = capabilities.get("output") if isinstance(capabilities, dict) else None
            if not isinstance(output, list) or "text" not in output:
                raise WeaverError("Kilo model must advertise text output")
        if not row["enabled"] or row.get("type", "chat") != "chat":
            raise WeaverError(f"{host} model must be enabled and support chat")
        if not isinstance(row.get("variants"), list):
            raise WeaverError(f"Invalid {host} model variants")
        variants: set[str] = set()
        for entry in row["variants"]:
            if not isinstance(entry, dict):
                raise WeaverError(f"Invalid {host} variant entry")
            identifier = _variant_id(entry.get("id"), host)
            if identifier in variants:
                raise WeaverError(f"Duplicate {host} model variant")
            variants.add(identifier)
        if selected_variant is not None and selected_variant not in variants:
            raise WeaverError(f"{host} variant is absent for the selected model")
        matched = True
    if not matched:
        raise WeaverError(f"{host} provider/model is absent from the saved response")
    selector = model if kilo or selected_variant is None else f"{model}#{selected_variant}"
    return selector, digest
