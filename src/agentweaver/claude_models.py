"""Inspect saved Claude Code model choices and stage validated settings."""

from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import _json_artifact, _load_bounded_snapshot, _model_id, _strings

CLAUDE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_FLAGS = {
    "supportsEffort": "supports_effort",
    "supportsAdaptiveThinking": "supports_adaptive_thinking",
    "supportsFastMode": "supports_fast_mode",
    "supportsAutoMode": "supports_auto_mode",
    "supportsThinkingOff": "supports_thinking_off",
}
_SDK_SOURCE = "https://code.claude.com/docs/en/agent-sdk/typescript#modelinfo"


def _selector(value: object) -> str:
    if isinstance(value, str) and value.endswith("[1m]"):
        _model_id(value.removesuffix("[1m]"))
        return value
    return _model_id(value)


def inspect_claude_models(path: Path, *, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
    """Report model selectors and capability hints from a saved ModelInfo array."""
    rows, digest = _load_bounded_snapshot(path, "Claude Code model catalog")
    if not isinstance(rows, list):
        raise WeaverError("Expected a Claude Code SDK ModelInfo JSON array")
    entries: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise WeaverError("Invalid Claude Code model entry")
        selector = _selector(row.get("value"))
        if selector in entries:
            raise WeaverError("Duplicate Claude Code model selector")
        resolved = row.get("resolvedModel")
        raw_levels = row.get("supportedEffortLevels")
        flags = {field: row.get(key) for key, field in _FLAGS.items()}
        if any(value is not None and type(value) is not bool for value in flags.values()):
            raise WeaverError("Invalid Claude Code model capability flag")
        entries[selector] = {
            "value": selector,
            "resolved_model": None if resolved is None else _selector(resolved),
            "supported_efforts": None if raw_levels is None else _strings(raw_levels, "Claude Code effort levels"),
            **flags,
        }
    selected = None
    if model is not None:
        selected = entries.get(_selector(model))
        if selected is None:
            raise WeaverError("Selected Claude Code model must match a saved selector value")
        if effort is not None and (
            selected["supports_effort"] is False or effort not in CLAUDE_EFFORTS
            or effort not in (selected["supported_efforts"] or [])
        ):
            raise WeaverError("Requested effort is unsupported or unknown for this Claude Code model")
    elif effort is not None:
        raise WeaverError("Claude Code reasoning effort requires an explicit model")
    return {
        "product": "claude-code", "source": "Claude Code Agent SDK ModelInfo",
        "snapshot_sha256": digest, "models": list(entries.values()), "selection": selected,
        "requested_effort": effort, "availability": "unknown", "catalog_completeness": "unknown",
        "inference_verified": False,
    }


def render_claude_model(path: Path, surface: str, model: str, effort: str | None) -> list[RenderedArtifact]:
    """Stage project settings, or one CLI invocation for a max effort choice."""
    report = inspect_claude_models(path, model=model, effort=effort)
    selector = report["selection"]["value"]
    if effort == "max":
        if surface != "cli":
            raise WeaverError("Claude Code max uses the CLI session arguments or the interactive model picker")
        artifact = _json_artifact("claude-code/model/session-arguments.json",
                                  ["--model", selector, "--effort", effort], "session-arguments")
        use = "Pass the staged arguments to claude for one session and supply your prompt separately."
    else:
        settings = {"model": selector}
        if effort is not None:
            settings["effortLevel"] = effort
        artifact = _json_artifact("claude-code/.claude/settings.json", settings, "model-config")
        use = "Review and merge the selected fields into the project's .claude/settings.json."
    note = (
        f"# Claude Code model setup\n\nTarget: {surface}\n\n{use} "
        "Preserve existing settings. Review model access, environment overrides and managed policy. "
        "The saved catalog describes its source session.\n\n"
        f"Saved ModelInfo SHA-256: {report['snapshot_sha256']}\n\n"
        f"- {_SDK_SOURCE}\n- https://code.claude.com/docs/en/model-config#adjust-effort-level\n"
    )
    return [artifact, RenderedArtifact(PurePosixPath("claude-code/model/model-setup.md"), note.encode(), "model-setup")]
