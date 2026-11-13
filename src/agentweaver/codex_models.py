"""Validate a Codex subagent selection against a saved app-server catalog."""
from __future__ import annotations

from pathlib import Path

from .config import WeaverError
from .models import _load_bounded_snapshot, _model_id, _text

CODEX_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})


def validate_codex_selection(path: Path, model: str, effort: str | None) -> str:
    """Check a picker-visible model and return the saved response's content hash."""
    reply, digest = _load_bounded_snapshot(path, "Codex model catalog")
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
    return digest
