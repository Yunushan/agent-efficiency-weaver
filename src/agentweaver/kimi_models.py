"""Inspect caller-supplied Kimi Code model aliases without contacting its server."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import WeaverError, _invalid_constant, _json_float, _json_object
from .models import _MAX_SNAPSHOT_BYTES, _scan, _strings, _text

_KIMI_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _alias(value: object) -> str:
    text = _text(value, "Kimi model alias")
    if len(text) > 250 or not text.strip():
        raise WeaverError("Invalid Kimi model alias")
    return text


def inspect_kimi_models(path: Path, *, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
    """Read a saved successful Kimi web GET /api/v1/models envelope."""
    def read_bounded() -> bytes:
        try:
            with path.open("rb") as stream:
                content = stream.read(_MAX_SNAPSHOT_BYTES + 1)
        except OSError:
            raise WeaverError("Cannot read Kimi model snapshot") from None
        if len(content) > _MAX_SNAPSHOT_BYTES:
            raise WeaverError("Kimi model snapshot exceeds 32 MiB")
        return content

    content = read_bounded()
    try:
        raw = json.loads(content.decode("utf-8"), object_pairs_hook=_json_object,
                         parse_constant=_invalid_constant, parse_float=_json_float)
    except (UnicodeError, ValueError, RecursionError):
        raise WeaverError("Cannot read valid UTF-8 JSON object") from None
    if not isinstance(raw, dict):
        raise WeaverError("Expected JSON object")
    if read_bounded() != content:
        raise WeaverError("Kimi model snapshot changed while reading")
    _scan(raw)
    if type(raw.get("code")) is not int or raw["code"] != 0:
        raise WeaverError("Kimi model snapshot is not a successful response")
    data = raw.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise WeaverError("Kimi model snapshot requires data.items")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data["items"]:
        if not isinstance(item, dict):
            raise WeaverError("Invalid Kimi model entry")
        alias = _alias(item.get("model"))
        if alias in seen:
            raise WeaverError("Duplicate Kimi model alias")
        seen.add(alias)
        raw_provider = item.get("provider")
        provider = None if raw_provider is None else ("" if raw_provider == "" else _text(raw_provider, "Kimi provider"))
        context = item.get("max_context_size")
        if context is not None and (type(context) is not int or context < 1):
            raise WeaverError("Invalid Kimi context size")
        capabilities = (None if item.get("capabilities") is None
                        else _strings(item["capabilities"], "Kimi capabilities"))
        efforts = None if item.get("support_efforts") is None else _strings(item["support_efforts"], "Kimi efforts")
        default = None if item.get("default_effort") is None else _text(item["default_effort"], "Kimi default effort")
        default_supported = None if default is None or efforts is None else default in efforts
        upgrade = item.get("upgrade")
        if upgrade is not None:
            if not isinstance(upgrade, dict):
                raise WeaverError("Invalid Kimi upgrade metadata")
            upgrade = {"model": None if upgrade.get("model") is None else _alias(upgrade["model"]),
                       "retirement_at": None if upgrade.get("retirement_at") is None
                       else _text(upgrade["retirement_at"], "Kimi retirement timestamp")}
        name = item.get("display_name")
        display = None if name is None else ("" if name == "" else _text(name, "Kimi display name"))
        models.append({"alias": alias, "provider": provider, "display_name": display,
                       "max_context_size": context, "capabilities": capabilities,
                       "support_efforts": efforts, "default_effort": default,
                       "default_effort_in_list": default_supported, "upgrade": upgrade})
    if effort is not None and model is None:
        raise WeaverError("Kimi effort requires an explicit model alias")
    selection: dict[str, str] = {}
    if model is not None:
        alias = _alias(model)
        selected = next((item for item in models if item["alias"] == alias), None)
        if selected is None:
            raise WeaverError("Kimi model alias is absent from this snapshot")
        selection["alias"] = alias
        if effort is not None:
            chosen = _text(effort, "Kimi thinking effort")
            if chosen not in _KIMI_EFFORTS or selected["support_efforts"] is None or chosen not in selected["support_efforts"]:
                raise WeaverError("Kimi effort is undocumented, unsupported or unknown for this alias")
            selection["effort"] = chosen
    return {"schema_version": 1, "source": "caller-supplied Kimi web GET /api/v1/models response",
            "sha256": hashlib.sha256(content).hexdigest(), "snapshot_completeness": "unknown",
            "scope": "Configured aliases at capture time; upstream account access and current availability are unknown",
            "models": models, "selection": selection, "network_used": False,
            "credentials_read": False, "inference_tested": False}
