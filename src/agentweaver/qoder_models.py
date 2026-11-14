"""Inspect saved Qoder Agent SDK model metadata without contacting Qoder."""

from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import _json_artifact, _load_bounded_snapshot, _model_id, _strings, _text

_DISABLED_EFFORTS = frozenset({"disabled", "off", "none"})


def _record(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WeaverError(f"Invalid Qoder {label} object")
    return value


def _flag(row: dict[str, Any], name: str) -> bool | None:
    value = row.get(name)
    if name in row and type(value) is not bool:
        raise WeaverError("Invalid Qoder model metadata flag")
    return value


def _effort_entries(row: dict[str, Any], enabled: dict[str, Any]) -> list[dict[str, Any]] | None:
    if "efforts" not in enabled and "efforts" not in row and "defaultEffort" not in row:
        return None
    defaults: dict[str, bool | None] = dict.fromkeys(_strings(row.get("efforts", []), "Qoder efforts"))
    for name, entry in _record(enabled.get("efforts", {}), "efforts").items():
        defaults[_text(name, "Qoder effort")] = _flag(_record(entry, "effort"), "is_default")
    if "defaultEffort" in row:
        defaults[_text(row["defaultEffort"], "Qoder default effort")] = True
    return [{"effort": name, "is_default": default} for name, default in defaults.items()]


def _token_count(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise WeaverError("Invalid Qoder context token count")
    return value


def _context_entries(row: dict[str, Any]) -> list[dict[str, Any]] | None:
    if not row.keys() & {"context_config", "availableContextWindows", "defaultContextWindow"}:
        return None
    direct = row.get("availableContextWindows", [])
    if not isinstance(direct, list):
        raise WeaverError("Invalid Qoder available context windows")
    default = _token_count(row["defaultContextWindow"]) if "defaultContextWindow" in row else None
    windows = []
    for label, raw in _record(row.get("context_config", {}), "context configuration").items():
        entry = _record(raw, "context tier")
        tokens = _token_count(entry.get("token_count"))
        is_default = _flag(entry, "is_default")
        windows.append({"label": _text(label, "Qoder context tier"), "token_count": tokens,
                        "is_default": tokens == default if default is not None else is_default})
    extra = dict.fromkeys(_token_count(value) for value in direct)
    if default is not None:
        extra.setdefault(default, None)
    tagged = {entry["token_count"] for entry in windows}
    windows.extend({"label": None, "token_count": tokens, "is_default": tokens == default if default is not None else None}
                   for tokens in extra if tokens not in tagged)
    return windows


def _model_entry(row: dict[str, Any]) -> dict[str, Any]:
    thinking = _record(row.get("thinking_config", {}), "thinking configuration")
    enabled = _record(thinking.get("enabled", {}), "enabled thinking")
    if "disabled" in thinking:
        _record(thinking["disabled"], "disabled thinking")
    supports_disabled = _flag(row, "supportsDisabled")
    if "disabled" in thinking:
        supports_disabled = True
    elif supports_disabled is None and "thinking_config" in row:
        supports_disabled = False
    return {
        "id": _model_id(row.get("value")),
        "display_name": _text(row["displayName"], "Qoder model display name") if "displayName" in row else None,
        "enabled": _flag(row, "isEnabled"),
        "thinking_enabled_advertised": "enabled" in thinking if "thinking_config" in row else None,
        "thinking_disabled_advertised": supports_disabled,
        "thinking_enabled_by_default": _flag(enabled, "is_default"),
        "efforts": _effort_entries(row, enabled),
        "context_windows": _context_entries(row),
    }


def inspect_qoder_models(path: Path, *, model: str | None = None, effort: str | None = None,
                         context_size: int | None = None) -> dict[str, Any]:
    """Read an SDK getAvailableModels() array and optionally validate exact selections."""
    listing, digest = _load_bounded_snapshot(path, "Qoder Agent SDK model catalog")
    if not isinstance(listing, list):
        raise WeaverError("Expected a Qoder Agent SDK getAvailableModels() JSON array")
    entries: dict[str, dict[str, Any]] = {}
    for raw in listing:
        entry = _model_entry(_record(raw, "model"))
        identifier = entry["id"]
        if identifier in entries:
            raise WeaverError("Duplicate Qoder model ID")
        entries[identifier] = entry

    selected = None
    if model is not None:
        selected = entries.get(_model_id(model))
        if selected is None or selected["enabled"] is not True:
            raise WeaverError("Selected Qoder model is absent or lacks confirmed enabled status in the snapshot")
        if effort is not None:
            supported = {entry["effort"] for entry in selected["efforts"] or []}
            if selected["thinking_disabled_advertised"]:
                supported.update(_DISABLED_EFFORTS)
            if effort not in supported:
                raise WeaverError("Requested effort is unsupported or unknown for this Qoder model")
        if context_size is not None and (type(context_size) is not int or context_size not in {
            entry["token_count"] for entry in selected["context_windows"] or []
        }):
            raise WeaverError("Requested context window is unsupported or unknown for this Qoder model")
    elif effort is not None or context_size is not None:
        raise WeaverError("Qoder effort and context selection require an explicit model")
    return {
        "product": "qoder",
        "surface": "cli",
        "source": "Qoder Agent SDK getAvailableModels()",
        "snapshot_sha256": digest,
        "models": list(entries.values()),
        "selection": selected,
        "requested_effort": effort,
        "requested_context_window": context_size,
        "availability": "unknown",
        "catalog_completeness": "unknown",
        "inference_verified": False,
    }


def render_qoder_cli_model(model: str, effort: str | None, context_size: int | None, *,
                           persistent: bool, catalog: Path | None = None) -> list[RenderedArtifact]:
    """Stage model settings using Qoder's CLI or settings-file vocabulary."""
    identifier = _model_id(model)
    efforts = {"low", "medium", "high", "xhigh", "max"}
    if persistent:
        efforts.update(_DISABLED_EFFORTS)
    if effort is not None and effort not in efforts:
        raise WeaverError("Unsupported Qoder reasoning effort for the selected output")
    if context_size is not None and (type(context_size) is not int or context_size < 1):
        raise WeaverError("Qoder context window must be a positive integer")
    evidence = "Confirm the model, effort and context in the target account."
    if catalog is not None:
        report = inspect_qoder_models(catalog, model=identifier, effort=effort, context_size=context_size)
        evidence = f"Saved Qoder Agent SDK model catalog SHA-256: {report['snapshot_sha256']}"

    if persistent:
        settings: dict[str, str | int] = {"name": identifier}
        if effort is not None:
            settings["reasoningEffort"] = effort
        if context_size is not None:
            settings["contextWindow"] = context_size
        output = _json_artifact("qoder/model/cli.fragment.json", {"model": settings}, "model-config")
        instructions = (
            "Review the fragment and pass its path to qoder --settings, or merge its fields into "
            "the model object in an existing Qoder settings file. "
            "Use .qoder/settings.json for a trusted project, or ~/.qoder/settings.json for user defaults. "
            "Preserve other settings, including MCP entries and existing custom model setup."
        )
    else:
        arguments = ["--model", identifier]
        if effort is not None:
            arguments.extend(["--reasoning-effort", effort])
        if context_size is not None:
            arguments.extend(["--context-window", str(context_size)])
        output = _json_artifact("qoder/model/session-arguments.json", arguments, "session-arguments")
        instructions = "Pass these arguments to qoder for one session."
    note = (
        "# Qoder CLI model setup\n\n"
        f"{instructions}\n\n"
        "Use the exact selection ID from the target account's qoder --list-models output or already-configured "
        "custom models. Review effective settings in /model.\n\n"
        f"{evidence}\n\n"
        "Weaver stages these files offline. Live account access and inference remain unverified.\n\n"
        "- https://docs.qoder.com/cli/model\n"
        "- https://docs.qoder.com/cli/cli-reference\n"
        "- https://docs.qoder.com/cli/settings-reference\n"
        "- https://docs.qoder.com/cli/settings\n"
    )
    return [output, RenderedArtifact(PurePosixPath("qoder/model/model-setup.md"), note.encode(), "model-setup")]
