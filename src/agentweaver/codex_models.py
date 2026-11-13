"""Validate a Codex subagent selection against a saved app-server catalog."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import WeaverError, _invalid_constant, _json_float, _json_object
from .models import _MAX_SNAPSHOT_BYTES, _model_id, _scan, _text

CODEX_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})


def validate_codex_selection(path: Path, model: str, effort: str | None) -> str:
    """Check a picker-visible model and return the saved response's content hash."""
    def read_bounded() -> bytes:
        try:
            with path.open("rb") as stream:
                data = stream.read(_MAX_SNAPSHOT_BYTES + 1)
        except OSError:
            raise WeaverError("Cannot read Codex model catalog") from None
        if len(data) > _MAX_SNAPSHOT_BYTES:
            raise WeaverError("Codex model catalog exceeds 32 MiB")
        return data

    data = read_bounded()
    try:
        reply = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object,
                           parse_constant=_invalid_constant, parse_float=_json_float)
    except (UnicodeError, ValueError, RecursionError):
        raise WeaverError("Cannot read valid UTF-8 Codex model catalog") from None
    if read_bounded() != data:
        raise WeaverError("Codex model catalog changed while reading")
    _scan(reply)
    result = reply.get("result") if isinstance(reply, dict) and "error" not in reply else None
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise WeaverError("Expected a successful Codex app-server model/list response")
    candidates: list[set[str] | None] = []
    seen: set[str] = set()
    for row in result["data"]:
        if not isinstance(row, dict) or type(row.get("hidden")) is not bool:
            raise WeaverError("Invalid Codex model entry")
        entry_id = _model_id(row.get("id"))
        model_id = _model_id(row.get("model"))
        if entry_id in seen:
            raise WeaverError("Duplicate Codex model entry ID")
        seen.add(entry_id)
        raw_efforts = row.get("supportedReasoningEfforts")
        efforts: set[str] | None = None
        if raw_efforts is not None:
            if not isinstance(raw_efforts, list):
                raise WeaverError("Invalid Codex model effort list")
            efforts = set()
            for choice in raw_efforts:
                if not isinstance(choice, dict):
                    raise WeaverError("Invalid Codex model effort entry")
                value = _text(choice.get("reasoningEffort"), "Codex effort")
                if value in efforts:
                    raise WeaverError("Duplicate Codex model effort")
                efforts.add(value)
        if model_id == model and not row["hidden"]:
            candidates.append(efforts)
    if len(candidates) != 1:
        raise WeaverError("Expected exactly one picker-visible Codex model match in the saved page")
    allowed = candidates[0]
    if effort is not None and (effort not in CODEX_EFFORTS or allowed is None or effort not in allowed):
        raise WeaverError("Codex effort is unsupported or unknown for the selected model")
    return hashlib.sha256(data).hexdigest()
