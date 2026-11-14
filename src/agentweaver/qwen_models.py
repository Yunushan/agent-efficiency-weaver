"""Read Qwen Code daemon workspace model and reasoning metadata offline."""

from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import _json_artifact, _load_bounded_snapshot, _model_id, _text

_STARTUP_EFFORTS = {"none", "default", "low", "medium", "high", "xhigh", "max"}


def _objects(value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise WeaverError(f"Invalid Qwen {label} list")
    return value


def _boolean(row: dict[str, Any], key: str) -> bool:
    value = row.get(key)
    if type(value) is not bool:
        raise WeaverError(f"Invalid Qwen {key} flag")
    return value


def _reasoning_options(model: dict[str, Any]) -> tuple[list[str] | None, str | None]:
    options = _objects(model.get("configOptions", []), "configuration option")
    reasoning = [option for option in options if option.get("id") == "reasoning_effort"]
    if not reasoning:
        return None, None
    if len(reasoning) != 1 or reasoning[0].get("type") != "select":
        raise WeaverError("Invalid Qwen reasoning control")
    control = reasoning[0]
    values = [_text(item.get("value"), "Qwen reasoning option")
              for item in _objects(control.get("options"), "reasoning option")]
    current = _text(control.get("currentValue"), "Qwen current reasoning value")
    if len(values) != len(set(values)) or current not in values:
        raise WeaverError("Inconsistent Qwen reasoning options")
    return values, current


def _route_id(value: object) -> str:
    identifier = _text(value, "Qwen model route")
    model, separator, auth = identifier.rpartition("(")
    if separator and auth.endswith(")"):
        _model_id(model)
        _model_id(auth[:-1])
        return identifier
    return _model_id(identifier)


def inspect_qwen_models(path: Path, *, auth_type: str | None = None, model: str | None = None,
                        effort: str | None = None) -> dict[str, Any]:
    """Inspect version 1 of the daemon's GET /workspace/providers response."""
    snapshot, digest = _load_bounded_snapshot(path, "Qwen workspace provider snapshot")
    if (not isinstance(snapshot, dict) or type(snapshot.get("v")) is not int or snapshot["v"] != 1
            or not isinstance(snapshot.get("workspaceCwd"), str) or not snapshot["workspaceCwd"]):
        raise WeaverError("Expected a Qwen Code v1 workspace providers response")
    initialized = _boolean(snapshot, "initialized")
    providers = _objects(snapshot.get("providers"), "provider")
    if not initialized and providers:
        raise WeaverError("Qwen uninitialized provider snapshot must have no providers")
    entries: list[dict[str, Any]] = []
    keys: set[tuple[str, str]] = set()
    for provider in providers:
        if provider.get("kind") != "model_provider":
            raise WeaverError("Invalid Qwen provider kind")
        provider_id = _model_id(provider.get("authType"))
        for row in _objects(provider.get("models"), "model"):
            route_id = _route_id(row.get("modelId"))
            key = (provider_id, route_id)
            if key in keys:
                raise WeaverError("Duplicate Qwen provider model route")
            keys.add(key)
            options, current_value = _reasoning_options(row)
            context = row.get("contextLimit")
            if "contextLimit" in row and (type(context) is not int or context < 1):
                raise WeaverError("Invalid Qwen context limit")
            entries.append({
                "auth_type": provider_id,
                "route_id": route_id,
                "model_id": _model_id(row.get("baseModelId")),
                "is_current": _boolean(row, "isCurrent"),
                "is_runtime_registered": _boolean(row, "isRuntime"),
                "context_limit": context,
                "reasoning_options": options,
                "reported_reasoning": current_value,
            })

    if auth_type is not None:
        auth_type = _model_id(auth_type)
    scoped = [entry for entry in entries if auth_type is None or entry["auth_type"] == auth_type]
    selected = None
    if model is not None:
        identifier = _route_id(model)
        matches = [entry for entry in scoped if entry["route_id"] == identifier]
        if not matches:
            matches = [entry for entry in scoped if entry["model_id"] == identifier]
        if not initialized or len(matches) != 1:
            raise WeaverError("Expected one Qwen model match in an initialized snapshot; use the exact auth type and route ID")
        selected = matches[0]
        if effort is not None and effort not in (selected["reasoning_options"] or []):
            raise WeaverError("Qwen workspace did not advertise this reasoning option for the selected route")
    elif effort is not None:
        raise WeaverError("Qwen reasoning option selection requires an explicit model")
    return {
        "product": "qwen-code",
        "source": "qwen serve GET /workspace/providers",
        "snapshot_sha256": digest,
        "initialized": initialized,
        "models": scoped,
        "selection": selected,
        "requested_reasoning_option": effort,
        "availability": "unknown",
        "catalog_completeness": "unknown",
        "inference_verified": False,
    }


def render_qwen_startup(path: Path, model: str, effort: str | None, *,
                        auth_type: str | None = None) -> list[RenderedArtifact]:
    """Stage an exact model and optional reasoning choice for a new daemon thread."""
    if effort is not None and effort not in _STARTUP_EFFORTS:
        raise WeaverError("Unsupported Qwen daemon startup reasoning value")
    report = inspect_qwen_models(path, auth_type=auth_type, model=model, effort=effort)
    selection = report["selection"]
    route = selection["route_id"]
    if len(route) > 256:
        raise WeaverError("Qwen daemon startup model route exceeds 256 characters")
    startup = {"modelServiceId": route}
    if effort is not None:
        startup["reasoningEffort"] = effort
    body = {"sessionScope": "thread", "startupConfig": startup}
    note = (
        "# Qwen Code daemon session startup\n\n"
        f"Saved workspace providers snapshot SHA-256: {report['snapshot_sha256']}\n\n"
        "Review the staged body for POST /session. Use the daemon that supplied the snapshot and "
        "confirm it advertises session_startup_config. Add the registered workspace cwd if it differs "
        "from the daemon's primary workspace. Supply authentication separately.\n\n"
        "Before sending a prompt, check modelApplied and startupConfigApplied in the creation response. "
        "Verify the canonical model route and any requested reasoning selection and effective state. "
        "Treat missing confirmation as an unresolved outcome. Weaver only stages the request body.\n\n"
        "- https://qwenlm.github.io/qwen-code-docs/en/developers/qwen-serve-protocol/\n"
    )
    return [
        _json_artifact("qwen-code/model/session-startup.json", body, "session-startup"),
        RenderedArtifact(PurePosixPath("qwen-code/model/model-setup.md"), note.encode(), "model-setup"),
    ]
