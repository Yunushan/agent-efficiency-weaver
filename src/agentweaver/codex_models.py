"""Inspect saved Codex app-server model pages and validate subagent selections."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import WeaverError
from .models import _load_bounded_snapshot, _model_id, _text

CODEX_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})


def _model_entry(row: object) -> dict[str, Any]:
    if not isinstance(row, dict) or type(row.get("hidden")) is not bool:
        raise WeaverError("Invalid Codex model entry")
    raw_efforts = row.get("supportedReasoningEfforts")
    efforts: list[str] | None = None
    if raw_efforts is not None:
        if not isinstance(raw_efforts, list):
            raise WeaverError("Invalid Codex model effort list")
        efforts = []
        for choice in raw_efforts:
            if not isinstance(choice, dict):
                raise WeaverError("Invalid Codex model effort entry")
            efforts.append(_text(choice.get("reasoningEffort"), "Codex effort"))
        if len(efforts) != len(set(efforts)):
            raise WeaverError("Duplicate Codex model effort")
    default = row.get("defaultReasoningEffort")
    return {
        "id": _model_id(row.get("id")),
        "model": _model_id(row.get("model")),
        "hidden": row["hidden"],
        "supported_efforts": efforts,
        "default_effort": None if default is None else _text(default, "Codex default effort"),
    }


def inspect_codex_models(path: Path, *, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
    """Return model and reasoning metadata from one reply or an ordered pages array."""
    snapshot, digest = _load_bounded_snapshot(path, "Codex model catalog")
    pages = snapshot["pages"] if isinstance(snapshot, dict) and set(snapshot) == {"pages"} else [snapshot]
    if not isinstance(pages, list) or not pages:
        raise WeaverError("Codex model catalog requires a non-empty pages array")

    models: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    more_pages: bool | None = None
    for index, reply in enumerate(pages):
        result = reply.get("result") if isinstance(reply, dict) and "error" not in reply else None
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise WeaverError("Expected a successful Codex app-server model/list response")
        cursor = result.get("nextCursor")
        if cursor is not None:
            cursor = _text(cursor, "Codex model cursor")
            if cursor in seen_cursors:
                raise WeaverError("Repeated Codex model page cursor")
            seen_cursors.add(cursor)
        if index < len(pages) - 1 and cursor is None:
            raise WeaverError("Codex model page is missing a continuation cursor")
        more_pages = cursor is not None if "nextCursor" in result else None

        for row in result["data"]:
            entry = _model_entry(row)
            if entry["id"] in seen_ids:
                raise WeaverError("Duplicate Codex model entry ID")
            seen_ids.add(entry["id"])
            models.append(entry)

    selection = None
    if model is not None:
        wanted = _model_id(model)
        matches = [entry for entry in models if entry["model"] == wanted and not entry["hidden"]]
        if len(matches) != 1:
            raise WeaverError("Expected exactly one picker-visible Codex model match in the saved catalog")
        selection = {"model": wanted}
        if effort is not None:
            if effort not in CODEX_EFFORTS or effort not in (matches[0]["supported_efforts"] or []):
                raise WeaverError("Codex effort is unsupported or unknown for the selected model")
            selection["model_reasoning_effort"] = effort
    elif effort is not None:
        raise WeaverError("Codex reasoning effort requires an explicit model")

    return {
        "product": "codex",
        "source": "app-server model/list",
        "snapshot_sha256": digest,
        "pages_read": len(pages),
        "more_pages_reported": more_pages,
        "models": models,
        "selection": selection,
        "catalog_completeness": "unknown",
        "availability": "unknown",
        "inference_verified": False,
    }


def validate_codex_selection(path: Path, model: str, effort: str | None) -> str:
    """Check a picker-visible selection and return the saved catalog's content hash."""
    digest: str = inspect_codex_models(path, model=model, effort=effort)["snapshot_sha256"]
    return digest
