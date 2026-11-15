"""Inspect saved Claude Code model choices and stage validated settings."""

import json
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


def _supports_effort(entry: dict[str, Any], effort: str) -> bool:
    return (entry["supports_effort"] is not False and effort in CLAUDE_EFFORTS
            and effort in (entry["supported_efforts"] or []))


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
        if effort is not None and not _supports_effort(selected, effort):
            raise WeaverError("Requested effort is unsupported or unknown for this Claude Code model")
    elif effort is not None:
        raise WeaverError("Claude Code reasoning effort requires an explicit model")
    return {
        "product": "claude-code", "source": "Claude Code Agent SDK ModelInfo",
        "snapshot_sha256": digest, "models": list(entries.values()), "selection": selected,
        "requested_effort": effort, "availability": "unknown", "catalog_completeness": "unknown",
        "inference_verified": False,
    }


def render_claude_model(path: Path, surface: str, model: str, effort: str | None, *,
                        ultracode: bool | None = None) -> list[RenderedArtifact]:
    """Stage project settings, or one CLI invocation for a max effort choice."""
    report = inspect_claude_models(path, model=model, effort=effort)
    selection = report["selection"]
    selector = selection["value"]
    if ultracode and not _supports_effort(selection, "xhigh"):
        raise WeaverError("Turning on Claude Code Ultracode requires a saved model selector with xhigh effort")
    settings: dict[str, Any] = {}
    if ultracode is not None:
        settings["ultracode"] = ultracode
    if effort == "max":
        if surface != "cli":
            raise WeaverError("Claude Code max uses the CLI session arguments or the interactive model picker")
        arguments = ["--model", selector, "--effort", effort]
        if settings:
            arguments.extend(["--settings", json.dumps(settings)])
        artifact = _json_artifact("claude-code/model/session-arguments.json", arguments, "session-arguments")
        use = "Pass the staged arguments to claude for one session and supply your prompt separately."
    else:
        if selector != "default":
            settings["model"] = selector
        if effort is not None:
            settings["effortLevel"] = effort
        artifact = _json_artifact("claude-code/.claude/settings.json", settings, "model-config")
        use = "Review and merge the selected fields into the project's .claude/settings.json."
        if selector == "default":
            use += " Remove any existing model override in the settings scope you are editing to use the runtime default."
    if ultracode:
        use += " The selected model must support xhigh and the local session must allow dynamic workflows."
    note = (
        f"# Claude Code model setup\n\nTarget: {surface}\n\n{use} "
        "Preserve existing settings. Review model access, environment overrides and managed policy. "
        "The saved catalog describes its source session.\n\n"
        f"Saved ModelInfo SHA-256: {report['snapshot_sha256']}\n\n"
        f"- {_SDK_SOURCE}\n- https://code.claude.com/docs/en/model-config#adjust-effort-level\n"
    )
    return [artifact, RenderedArtifact(PurePosixPath("claude-code/model/model-setup.md"), note.encode(), "model-setup")]
