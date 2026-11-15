"""Read saved Bionic model picker rows and prepare a manual selection."""

from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import _json_artifact, _load_bounded_snapshot, _text

BIONIC_MODEL_SOURCES = ("local", "cloud", "lmLink")
_PICKER_LABELS = {"off": "Off", "low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high"}
_SOURCE_LABELS = {"local": "Local", "cloud": "Cloud", "lmLink": "Remote (LM Link)"}
_DOC = "https://lmstudio.ai/docs/bionic/models"


def _picker_model(row: object) -> dict[str, Any]:
    if not isinstance(row, dict) or row.get("source") not in BIONIC_MODEL_SOURCES:
        raise WeaverError("Invalid Bionic picker model source")
    source = row["source"]
    model_type, id_field = ("cloudInference", "model") if source == "cloud" else ("indexed", "indexedModelIdentifier")
    specifier = row.get("modelSpecifier")
    if (not isinstance(specifier, dict) or set(specifier) != {"type", id_field}
            or specifier.get("type") != model_type):
        raise WeaverError("Bionic model specifier must match its picker source")
    model_id = _text(specifier[id_field], "Bionic picker model ID")
    mapping = row.get("reasoningLevelMapping")
    if not isinstance(mapping, dict) or set(mapping) != set(_PICKER_LABELS):
        raise WeaverError("Expected Bionic's five reasoning mapping entries")
    native = {}
    aliases = {}
    for level, label in _PICKER_LABELS.items():
        item = mapping[level]
        if not isinstance(item, dict):
            raise WeaverError("Invalid Bionic reasoning mapping")
        if item.get("type") == "native":
            native[level] = _text(item.get("label", label), "Bionic reasoning label")
        elif item.get("type") == "mapTo" and item.get("mapTo") in tuple(_PICKER_LABELS):
            aliases[level] = item["mapTo"]
        else:
            raise WeaverError("Invalid Bionic reasoning mapping")
    for field in ("vision", "trainedForToolUse"):
        if type(row.get(field)) is not bool:
            raise WeaverError("Invalid Bionic model capability flag")
    return {"id": model_id, "model_specifier": {"type": model_type, id_field: model_id},
            "source": source, "display_name": _text(row.get("displayName"), "Bionic model display name"),
            "reasoning_levels": native, "mapped_reasoning_levels": aliases,
            "picker_options": native or dict(_PICKER_LABELS), "vision": row["vision"],
            "trained_for_tool_use": row["trainedForToolUse"],
            **{field: _text(row[field], f"Bionic model {field}")
               for field in ("format", "quantization") if field in row}}


def inspect_bionic_models(path: Path, model: str | None = None, *, source: str | None = None,
                          effort: str | None = None) -> dict[str, Any]:
    """Normalize saved options using the Bionic Runner Link picker schema."""
    data, digest = _load_bounded_snapshot(path, "Bionic model picker snapshot")
    if not isinstance(data, list):
        raise WeaverError("Expected a Bionic modelSelectionOptions array")
    models = [_picker_model(row) for row in data]
    result: dict[str, Any] = {
        "schema_version": 1, "models": models, "model_count": len(models), "snapshot_sha256": digest,
        "catalog_completeness": "unknown", "network_used": False, "inference_tested": False,
    }
    if model is None:
        if source is not None or effort is not None:
            raise WeaverError("Bionic source and reasoning selection require a model ID")
        return result
    matches = [row for row in models if row["id"] == model and (source is None or row["source"] == source)]
    if len(matches) != 1:
        raise WeaverError("Expected one exact Bionic model ID; use its source or narrow the saved picker listing")
    selected = matches[0].copy()
    if effort is not None:
        options = selected["picker_options"]
        if len(options) < 2 or effort not in options:
            raise WeaverError("Requested Bionic reasoning level is unavailable as an explicit picker option")
        selected["reasoning_selection"] = {"level": effort, "label": options[effort]}
    result["selection"] = selected
    return result


def render_bionic_model(path: Path, model: str, *, source: str | None = None,
                        effort: str | None = None) -> list[RenderedArtifact]:
    inspected = inspect_bionic_models(path, model, source=source, effort=effort)
    selected = inspected["selection"]
    prefix = PurePosixPath("lm-studio/bionic/model")
    notes = [
        "# Bionic model selection", "",
        f"Open the session model picker and choose {_SOURCE_LABELS[selected['source']]}. "
        "Use the model display name and exact ID in the adjacent selection JSON.", "",
    ]
    if effort is not None:
        notes.extend(["Choose the reasoning option matching the staged label in the Bionic picker.", ""])
    notes.extend([
        "To set a default for new sessions, review Settings > General > Root model.",
        "Review current model availability in Bionic. The JSON is a manual selection record.", "",
        f"[Bionic model picker]({_DOC})", "",
    ])
    return [
        RenderedArtifact(prefix / "MODEL-HANDOFF.md", "\n".join(notes).encode(), "handoff"),
        _json_artifact((prefix / "model-selection.json").as_posix(),
                       {"selection": selected, "snapshot_sha256": inspected["snapshot_sha256"]}, "model-selection"),
    ]
