"""Inspect caller-supplied Perplexity Computer model choices without contacting MCP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import WeaverError, _invalid_constant, _json_float, _json_object
from .models import _MAX_SNAPSHOT_BYTES, _model_id, _scan, _strings, _text

_COMPUTER_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_COMPUTER_MODES = {"light", "standard", "high", "ultra"}


def _optional_text(value: object, label: str) -> str | None:
    return None if value is None else _text(value, label)


def _description(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2000:
        raise WeaverError("Invalid Computer model description")
    if not value:
        return value
    _text(value.replace("\r\n", " ").replace("\n", " ").replace("\t", " "), "model description")
    return value


def _effort(value: object) -> str | None:
    if value is None:
        return None
    text = _text(value, "Computer effort")
    if text not in _COMPUTER_EFFORTS:
        raise WeaverError("Unsupported Computer effort")
    return text


def inspect_computer_models(path: Path, *, model: str | None = None, mode: str | None = None,
                            effort: str | None = None) -> dict[str, Any]:
    """Validate a saved models_list response and optional account-scoped choice."""
    def read_bounded() -> bytes:
        try:
            with path.open("rb") as stream:
                data = stream.read(_MAX_SNAPSHOT_BYTES + 1)
        except OSError:
            raise WeaverError("Cannot read Computer model snapshot") from None
        if len(data) > _MAX_SNAPSHOT_BYTES:
            raise WeaverError("Computer model snapshot exceeds 32 MiB")
        return data

    content = read_bounded()
    try:
        raw = json.loads(content.decode("utf-8"), object_pairs_hook=_json_object,
                         parse_constant=_invalid_constant, parse_float=_json_float)
    except (UnicodeError, ValueError, RecursionError):
        raise WeaverError("Cannot read valid UTF-8 JSON object") from None
    if not isinstance(raw, dict):
        raise WeaverError("Expected JSON object")
    if read_bounded() != content:
        raise WeaverError("Computer model snapshot changed while reading")
    _scan(raw)
    if not isinstance(raw.get("models"), list) or not isinstance(raw.get("modes"), list):
        raise WeaverError("Computer model snapshot requires models and modes lists")
    models: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    for item in raw["models"]:
        if not isinstance(item, dict):
            raise WeaverError("Invalid Computer model entry")
        identifier = _model_id(item.get("id"))
        if identifier in seen_models:
            raise WeaverError("Duplicate Computer model id")
        seen_models.add(identifier)
        fast = item.get("fast")
        if fast is not None and type(fast) is not bool:
            raise WeaverError("Invalid Computer fast flag")
        efforts = None if item.get("efforts") is None else _strings(item["efforts"], "Computer efforts")
        default = _optional_text(item.get("default_effort"), "default Computer effort")
        if default is not None and efforts is not None and default not in efforts:
            raise WeaverError("Computer default effort is absent from the model's efforts")
        label = item.get("label")
        models.append({"id": identifier, "label": "" if label == "" else _optional_text(label, "model label"),
                       "description": _description(item.get("description")),
                       "fast": fast, "efforts": efforts, "default_effort": default})
    modes: list[dict[str, Any]] = []
    seen_modes: set[str] = set()
    for item in raw["modes"]:
        if not isinstance(item, dict):
            raise WeaverError("Invalid Computer mode entry")
        name = _text(item.get("mode"), "Computer mode")
        if name in seen_modes:
            raise WeaverError("Duplicate Computer mode")
        seen_modes.add(name)
        modes.append({"mode": name, "model": None if item.get("model") is None else _model_id(item["model"]),
                      "effort": _optional_text(item.get("effort"), "Computer mode effort")})
    default_model = None if raw.get("default_model") is None else _model_id(raw["default_model"])
    default_mode = _optional_text(raw.get("default_mode"), "default Computer mode")
    if model is not None and mode is not None:
        raise WeaverError("Computer mode and model cannot be combined")
    if effort is not None and model is None:
        raise WeaverError("Computer effort requires an explicit model")
    fields: dict[str, str] = {}
    if mode is not None:
        name = _text(mode, "Computer mode")
        if name not in seen_modes or name not in _COMPUTER_MODES:
            raise WeaverError("Computer mode is undocumented or absent from this snapshot")
        fields["mode"] = name
    if model is not None:
        identifier = _model_id(model)
        selected = next((item for item in models if item["id"] == identifier), None)
        if selected is None:
            raise WeaverError("Computer model is absent from this snapshot")
        fields["model"] = identifier
        if effort is not None:
            selected_effort = _effort(effort)
            if selected_effort is None or selected["efforts"] is None or selected_effort not in selected["efforts"]:
                raise WeaverError("Computer effort is unsupported or unknown for this model")
            fields["effort"] = selected_effort
    return {"schema_version": 1, "source": "caller-supplied Perplexity Computer MCP models_list response",
            "sha256": hashlib.sha256(content).hexdigest(), "snapshot_completeness": "unknown",
            "scope": "Snapshot account, plan, region and organization policy at capture time; current access is unknown",
            "models": models, "modes": modes, "default_model": default_model, "default_mode": default_mode,
            "selection_fields": fields, "network_used": False, "credentials_read": False, "inference_tested": False}
