"""Inspect a saved Factory Droid SDK model list and stage managed exec arguments."""

from pathlib import Path, PurePosixPath
from typing import Any

from .adapters import json_bytes
from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import _load_bounded_snapshot, _model_id, _strings, _text

_EFFORTS = {"none", "dynamic", "off", "minimal", "low", "medium", "high", "xhigh", "max"}
_SDK_SOURCE = "https://docs.factory.com/sdk/typescript#discover-available-models"


def inspect_factory_models(path: Path, *, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
    """Return only model IDs, availability flags, and reasoning metadata."""
    listing, digest = _load_bounded_snapshot(path, "Factory Droid SDK model catalog")
    if not isinstance(listing, list):
        raise WeaverError("Expected a Factory Droid SDK listModels() JSON array")
    entries: dict[str, dict[str, Any]] = {}
    for row in listing:
        if not isinstance(row, dict):
            raise WeaverError("Invalid Factory Droid model entry")
        disabled = row.get("disabled", False)
        custom = row.get("isCustom", False)
        if type(disabled) is not bool or type(custom) is not bool:
            raise WeaverError("Invalid Factory Droid model flags")
        if disabled:
            _text(row.get("disabledReason"), "Factory Droid disabled reason")
        elif "disabledReason" in row:
            raise WeaverError("A selectable Factory Droid model cannot have a disabled reason")
        identifier = _model_id(row.get("id"))
        if identifier in entries:
            raise WeaverError("Duplicate Factory Droid model ID")
        supported = row.get("supportedReasoningEfforts")
        default = row.get("defaultReasoningEffort")
        entries[identifier] = {
            "id": identifier,
            "disabled": disabled,
            "is_custom": custom or identifier.casefold().startswith("custom:"),
            "supported_efforts": None if supported is None else _strings(supported, "Factory Droid efforts"),
            "default_effort": None if default is None else _text(default, "Factory Droid default effort"),
        }
    selected = None
    if model is not None:
        selected = entries.get(_model_id(model))
        if selected is None or selected["disabled"]:
            raise WeaverError("Selected Factory Droid model is absent or disabled in the saved catalog")
        if effort is not None and (effort not in _EFFORTS or effort not in (selected["supported_efforts"] or [])):
            raise WeaverError("Requested effort is unsupported or unknown for this Factory Droid model")
    elif effort is not None:
        raise WeaverError("Factory Droid reasoning effort requires an explicit model")
    return {
        "product": "factory",
        "surface": "cli",
        "source": "Droid TypeScript SDK listModels()",
        "snapshot_sha256": digest,
        "models": list(entries.values()),
        "selection": selected,
        "requested_effort": effort,
        "availability": "unknown",
        "catalog_completeness": "unknown",
        "inference_verified": False,
    }


def render_factory_exec(path: Path, model: str, effort: str | None) -> list[RenderedArtifact]:
    """Stage one managed-model exec selection as an argv array."""
    report = inspect_factory_models(path, model=model, effort=effort)
    if report["selection"]["is_custom"] or model.casefold().startswith("custom:"):
        raise WeaverError("Factory Droid exec staging requires a managed model ID")
    arguments = ["exec", "--model", model]
    if effort is not None:
        arguments.extend(["--reasoning-effort", effort])
    note = (
        "# Factory Droid exec model setup\n\n"
        "Review the model and optional effort against the target Droid instance. "
        "Pass the staged arguments to droid and supply your prompt separately. "
        "Weaver does not launch Droid or send a model request.\n\n"
        f"Saved SDK catalog SHA-256: {report['snapshot_sha256']}\n\n"
        f"- {_SDK_SOURCE}\n"
        "- https://docs.factory.com/droid-exec/overview#models-and-reasoning-effort\n"
    )
    return [
        RenderedArtifact(PurePosixPath("factory/model/session-arguments.json"), json_bytes(arguments), "session-arguments"),
        RenderedArtifact(PurePosixPath("factory/model/model-setup.md"), note.encode(), "model-setup"),
    ]
