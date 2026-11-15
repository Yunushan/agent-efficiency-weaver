"""Inspect a saved Factory Droid SDK model list and stage exec arguments."""

import re
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters import json_bytes
from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import _load_bounded_snapshot, _model_id, _strings, _text

_EFFORTS = {"none", "dynamic", "off", "minimal", "low", "medium", "high", "xhigh", "max"}
_SDK_SOURCE = "https://docs.factory.com/sdk/typescript#discover-available-models"


def _selector_id(value: object) -> str:
    identifier = _text(value, "Factory Droid model selector")
    if not identifier.casefold().startswith("custom:") or not any(char in identifier for char in "[]"):
        return _model_id(identifier)
    # Droid keeps bracketed display-name suffixes in its positional custom model IDs.
    _model_id(identifier.replace("[", "_").replace("]", "_"))
    if re.fullmatch(r"custom:(?:[^\[\]]|\[[^\[\]]+\])+", identifier, flags=re.IGNORECASE) is None:
        raise WeaverError("Invalid Factory Droid custom model selector")
    return identifier


def _select(entries: dict[str, dict[str, Any]], model: str, effort: str | None, *,
            role: str = "Factory Droid") -> dict[str, Any]:
    selected = entries.get(_selector_id(model))
    if selected is None or selected["disabled"]:
        raise WeaverError(f"Selected {role} model is absent or disabled in the saved catalog")
    if effort is not None and (effort not in _EFFORTS or effort not in (selected["supported_efforts"] or [])):
        raise WeaverError(f"Requested effort is unsupported or unknown for this {role} model")
    return selected


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
        identifier = _selector_id(row.get("id"))
        if identifier in entries:
            raise WeaverError("Duplicate Factory Droid model ID")
        supported = row.get("supportedReasoningEfforts")
        default = row.get("defaultReasoningEffort")
        base = row.get("behavesLikeModelId")
        entries[identifier] = {
            "id": identifier,
            "disabled": disabled,
            "is_custom": custom or identifier.casefold().startswith("custom:"),
            "supported_efforts": None if supported is None else _strings(supported, "Factory Droid efforts"),
            "default_effort": None if default is None else _text(default, "Factory Droid default effort"),
            "behaves_like_model_id": None if base is None else _model_id(base),
        }
    selected = None
    if model is not None:
        selected = _select(entries, model, effort)
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


def render_factory_exec(path: Path, model: str, effort: str | None, *, spec_model: str | None = None,
                        spec_effort: str | None = None) -> list[RenderedArtifact]:
    """Stage one catalog-validated exec selection as an argv array."""
    report = inspect_factory_models(path, model=model, effort=effort)
    if spec_effort is not None and spec_model is None:
        raise WeaverError("Factory Droid specification effort requires an explicit specification model")
    arguments = ["exec", "--model", model]
    session_settings = {"modelId": model}
    if effort is not None:
        arguments.extend(["--reasoning-effort", effort])
        session_settings["reasoningEffort"] = effort
    custom = report["selection"]["is_custom"]
    if spec_model is not None:
        spec = _select({entry["id"]: entry for entry in report["models"]}, spec_model, spec_effort,
                       role="Factory Droid specification")
        custom = custom or spec["is_custom"]
        arguments.extend(["--use-spec", "--spec-model", spec_model])
        session_settings.update(interactionMode="spec", specModeModelId=spec_model)
        if spec_effort is not None:
            arguments.extend(["--spec-reasoning-effort", spec_effort])
            session_settings["specModeReasoningEffort"] = spec_effort
    note = (
        "# Factory Droid exec model setup\n\n"
        "Review the model and optional effort against the target Droid instance. "
        "Pass the staged arguments to droid and supply your prompt separately.\n\n"
    )
    if effort is None:
        note += (
            "Droid resolves the effort when it is omitted. The saved catalog records the advertised default. "
            "Supply an explicit supported effort when the request must use a particular level.\n\n"
        )
    if spec_model is not None:
        note += (
            "The staged arguments start in specification mode. Review the plan before approving execution.\n\n"
            "For a stream JSON-RPC client, merge `session-settings.json` into the `droid.initialize_session` "
            "parameters. Supply the session's machine ID, working directory, and permission settings separately. "
            "Review the plan before answering `droid.request_permission`.\n\n"
        )
    if custom:
        note += (
            "Keep the same custom model configuration and order as the saved catalog. "
            "Refresh the catalog and regenerate these arguments if the configured models change.\n\n"
        )
    note += (
        "Weaver does not launch Droid or send a model request.\n\n"
        f"Saved SDK catalog SHA-256: {report['snapshot_sha256']}\n\n"
        f"- {_SDK_SOURCE}\n"
        "- https://docs.factory.com/droid-exec/overview#models-and-reasoning-effort\n"
    )
    artifacts = [
        RenderedArtifact(PurePosixPath("factory/model/session-arguments.json"), json_bytes(arguments), "session-arguments"),
        RenderedArtifact(PurePosixPath("factory/model/model-setup.md"), note.encode(), "model-setup"),
    ]
    if spec_model is not None:
        artifacts.append(RenderedArtifact(PurePosixPath("factory/model/session-settings.json"),
                                          json_bytes(session_settings), "session-settings"))
    return artifacts
