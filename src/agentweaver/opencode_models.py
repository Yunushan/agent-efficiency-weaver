"""Verify an OpenCode V2 model selector against a saved model-list response."""
from __future__ import annotations

from pathlib import Path

from .config import WeaverError
from .models import _load_bounded_snapshot, _model_id, _text


def _variant_id(value: object) -> str:
    variant = _text(value, "OpenCode variant ID")
    if not variant.strip() or "#" in variant:
        raise WeaverError("Invalid OpenCode variant ID")
    return variant


def validate_opencode_selection(path: Path, model: str, variant: str | None) -> tuple[str, str]:
    """Return a validated V2 selector and the catalog response's SHA-256."""
    provider, separator, alias = model.partition("/")
    if not separator:
        raise WeaverError("An OpenCode selector requires provider/model")
    _model_id(provider)
    _model_id(alias)
    selected_variant = _variant_id(variant) if variant is not None else None
    reply, digest = _load_bounded_snapshot(path, "OpenCode model catalog")
    if not isinstance(reply, dict) or not isinstance(reply.get("location"), dict) or not isinstance(reply.get("data"), list):
        raise WeaverError("Expected a saved OpenCode V2 GET /api/model response")
    seen: set[tuple[str, str]] = set()
    matched = False
    for row in reply["data"]:
        if not isinstance(row, dict) or type(row.get("enabled")) is not bool:
            raise WeaverError("Invalid OpenCode model entry")
        provider_id = _model_id(row.get("providerID"))
        entry_id = _model_id(row.get("id"))
        if "/" in provider_id:
            raise WeaverError("Invalid OpenCode provider ID")
        key = (provider_id, entry_id)
        if key in seen:
            raise WeaverError("Duplicate OpenCode provider and model entry")
        seen.add(key)
        if key != (provider, alias):
            continue
        if not row["enabled"] or row.get("type", "chat") != "chat":
            raise WeaverError("OpenCode model must be enabled and support chat")
        if not isinstance(row.get("variants"), list):
            raise WeaverError("Invalid OpenCode model variants")
        variants: set[str] = set()
        for entry in row["variants"]:
            if not isinstance(entry, dict):
                raise WeaverError("Invalid OpenCode variant entry")
            identifier = _variant_id(entry.get("id"))
            if identifier in variants:
                raise WeaverError("Duplicate OpenCode model variant")
            variants.add(identifier)
        if selected_variant is not None and selected_variant not in variants:
            raise WeaverError("OpenCode variant is absent for the selected model")
        matched = True
    if not matched:
        raise WeaverError("OpenCode provider/model is absent from the saved response")
    selector = model if selected_variant is None else f"{model}#{selected_variant}"
    return selector, digest
