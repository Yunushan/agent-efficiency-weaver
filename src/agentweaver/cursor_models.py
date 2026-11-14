"""Read saved Cursor SDK model metadata and construct subagent selectors."""
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import WeaverError
from .models import _load_bounded_snapshot, _model_id, _strings, _text

_SDK_SELECTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,119}\Z")
_REASONING_PARAMETERS = {"effort", "reasoning"}


def _selector_token(value: object) -> str:
    token = _text(value, "Cursor parameter")
    if not _SDK_SELECTOR.fullmatch(token) or token.casefold().startswith(("crsr_", "sk-", "bearer")):
        raise WeaverError("Invalid or unsafe Cursor parameter")
    return token


def _cursor_id(value: object) -> str:
    identifier = _model_id(value)
    if identifier.casefold().startswith("crsr_"):
        raise WeaverError("Invalid or unsafe Cursor model ID")
    return identifier


def _unique_params(pairs: Iterable[tuple[object, object]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in pairs:
        identifier = _selector_token(key)
        if identifier in result:
            raise WeaverError("Duplicate Cursor model parameter")
        result[identifier] = _selector_token(value)
    return result


def _model_row(row: object) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise WeaverError("Invalid Cursor model entry")
    identifier = _cursor_id(row.get("id"))
    aliases = [_cursor_id(alias) for alias in _strings(row.get("aliases", []), "Cursor aliases")]
    parameters: dict[str, list[str]] = {}
    definitions = row.get("parameters", [])
    variants = row.get("variants", [])
    if not isinstance(definitions, list) or not isinstance(variants, list):
        raise WeaverError("Invalid Cursor parameter definitions or variants")
    for definition in definitions:
        if not isinstance(definition, dict) or not isinstance(definition.get("values"), list):
            raise WeaverError("Invalid Cursor parameter definition")
        key = _selector_token(definition.get("id"))
        if key in parameters:
            raise WeaverError("Duplicate Cursor parameter definition")
        choices = definition["values"]
        if not choices or any(not isinstance(choice, dict) for choice in choices):
            raise WeaverError("Invalid Cursor parameter values")
        values = [_selector_token(choice.get("value")) for choice in choices]
        if len(values) != len(set(values)):
            raise WeaverError("Duplicate Cursor parameter value")
        parameters[key] = values
    presets = []
    for variant in variants:
        if not isinstance(variant, dict) or not isinstance(variant.get("params"), list):
            raise WeaverError("Invalid Cursor model variant")
        pairs = variant["params"]
        if any(not isinstance(pair, dict) for pair in pairs):
            raise WeaverError("Invalid Cursor variant parameter")
        params = _unique_params((pair.get("id"), pair.get("value")) for pair in pairs)
        default = variant.get("isDefault")
        if default is not None and type(default) is not bool:
            raise WeaverError("Invalid Cursor variant default flag")
        if any(key in parameters and value not in parameters[key] for key, value in params.items()):
            raise WeaverError("Cursor variant conflicts with the parameter definitions")
        presets.append({"name": _text(variant.get("displayName"), "Cursor variant name"),
                        "parameters": params, "default": default})
    return {"id": identifier, "display_name": _text(row.get("displayName"), "Cursor model name"),
            "aliases": aliases, "parameters": parameters, "variants": presets}


def inspect_cursor_models(path: Path, *, model: str | None = None, effort: str | None = None,
                          parameters: list[str] | None = None) -> dict[str, Any]:
    """Inspect the SDK JSON array and validate explicit selections without authentication."""
    snapshot, digest = _load_bounded_snapshot(path, "Cursor SDK model catalog")
    if not isinstance(snapshot, list):
        raise WeaverError("Expected a Cursor SDK models.list() JSON array")
    models: dict[str, dict[str, Any]] = {}
    for raw in snapshot:
        row = _model_row(raw)
        if row["id"] in models:
            raise WeaverError("Duplicate Cursor model ID")
        models[row["id"]] = row
    selection = None
    if model is None and (effort is not None or parameters):
        raise WeaverError("Cursor parameters require an explicit model")
    if model is not None:
        identifier = _cursor_id(model)
        entry = models.get(identifier)
        if entry is None or identifier == "inherit":
            raise WeaverError("Use an exact model ID from the saved Cursor catalog")
        pairs = []
        for token in parameters or []:
            key, separator, value = _text(token, "Cursor model parameter").partition("=")
            if not separator:
                raise WeaverError("Cursor model parameters require ID=VALUE")
            pairs.append((key, value))
        requested = _unique_params(pairs)
        definitions = entry["parameters"]
        presets = [variant["parameters"] for variant in entry["variants"]]
        if effort is not None:
            keys = _REASONING_PARAMETERS & (set(definitions) | {key for preset in presets for key in preset})
            if len(keys) != 1 or requested.keys() & _REASONING_PARAMETERS:
                raise WeaverError("Specify the model's exact reasoning parameter with --cursor-param")
            requested[keys.pop()] = _selector_token(effort)
        if requested not in presets and any(value not in definitions.get(key, []) for key, value in requested.items()):
            raise WeaverError("Cursor parameter selection is unsupported or unknown for the selected model")
        options = ",".join(f"{key}={value}" for key, value in requested.items())
        selector = f"{identifier}[{options}]" if options else identifier
        selection = {"model": identifier, "parameters": requested, "subagent_selector": selector}
    return {"schema_version": 1, "source": "caller-supplied Cursor SDK models.list() array",
            "snapshot_sha256": digest, "snapshot_completeness": "unknown", "models": list(models.values()),
            "selection": selection, "network_used": False, "credentials_read": False, "inference_tested": False}
