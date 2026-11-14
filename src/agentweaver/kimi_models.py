"""Inspect and stage Kimi Code model selections from caller-supplied snapshots."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import RenderedArtifact
from .config import WeaverError, safe_name
from .models import _json_artifact, _load_bounded_snapshot, _strings, _text

_KIMI_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _alias(value: object) -> str:
    text = _text(value, "Kimi model alias")
    if len(text) > 250 or not text.strip():
        raise WeaverError("Invalid Kimi model alias")
    return text


def _validated_effort(model: dict[str, Any], effort: str) -> str:
    chosen = _text(effort, "Kimi thinking effort")
    if chosen not in _KIMI_EFFORTS or chosen not in (model["support_efforts"] or []):
        raise WeaverError(f"Kimi effort is undocumented, unsupported or unknown for alias {model['alias']!r}")
    return chosen


def inspect_kimi_models(path: Path, *, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
    """Read a saved successful Kimi web GET /api/v1/models envelope."""
    raw, digest = _load_bounded_snapshot(path, "Kimi model snapshot")
    if not isinstance(raw, dict):
        raise WeaverError("Expected JSON object")
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
            selection["effort"] = _validated_effort(selected, effort)
    return {"schema_version": 1, "source": "caller-supplied Kimi web GET /api/v1/models response",
            "sha256": digest, "snapshot_completeness": "unknown",
            "scope": "Configured aliases at capture time; upstream account access and current availability are unknown",
            "models": models, "selection": selection, "network_used": False,
            "credentials_read": False, "inference_tested": False}


def render_kimi_session(path: Path, model: str, effort: str | None, *,
                        workspace_id: str, profile: str | None = None) -> list[RenderedArtifact]:
    """Stage a model-bound Kimi web session creation body."""
    workspace = _text(workspace_id, "Kimi workspace ID")
    if len(workspace) > 250 or not safe_name(workspace):
        raise WeaverError("Invalid registered Kimi workspace ID")
    if profile is not None:
        _text(profile, "Kimi agent profile")
        if len(profile) > 64 or not safe_name(profile):
            raise WeaverError("Invalid Kimi agent profile name")
    report = inspect_kimi_models(path, model=model, effort=effort)
    selected = report["selection"]
    config = {"model": selected["alias"]}
    if "effort" in selected:
        config["thinking"] = selected["effort"]
    if profile is not None:
        config["profile"] = profile
    body = {"workspace_id": workspace, "environment_id": "local", "agent_config": config}
    source = "https://moonshotai.github.io/kimi-code/en/reference/server-api-sessions.html"
    note = (
        "# Kimi Code web session setup\n\n"
        "Review the staged body for POST /api/v1/sessions against the local server's /openapi.json. "
        "Supply authentication separately. Verify the workspace, model alias, and any named agent "
        "profile against that server before use. The model catalog covers model aliases and thinking efforts.\n\n"
        "Check the creation response before sending a prompt. Weaver only stages the body.\n\n"
        f"Model snapshot SHA-256: {report['sha256']}\n\n"
        f"- {source}\n"
    )
    return [
        _json_artifact("kimi-code/model/session-create.json", body, "session-create"),
        RenderedArtifact(PurePosixPath("kimi-code/model/model-setup.md"), note.encode(), "model-setup"),
    ]


def render_kimi_subagent_pool(path: Path, model: str, effort: str | None, *,
                              alternatives: list[str]) -> list[RenderedArtifact]:
    """Stage the secondary model section using aliases from one saved catalog."""
    report = inspect_kimi_models(path, model=model, effort=effort)
    selected = report["selection"]
    default = selected["alias"]
    aliases = [default, *map(_alias, alternatives)]
    if len(set(aliases)) != len(aliases):
        raise WeaverError("Duplicate Kimi subagent pool alias")
    models = {entry["alias"]: entry for entry in report["models"]}
    for alias in aliases[1:]:
        if alias not in models:
            raise WeaverError(f"Kimi subagent pool alias {alias!r} is absent from this snapshot")
        if effort is not None:
            _validated_effort(models[alias], effort)

    lines = ["[secondary_model]", f"default_model = {json.dumps(default, ensure_ascii=False)}"]
    if "effort" in selected:
        lines.append(f"default_effort = {json.dumps(selected['effort'])}")
    if alternatives:
        lines.extend(["", "[secondary_model.models]"])
        lines.extend(f'{json.dumps(alias, ensure_ascii=False)} = ""' for alias in aliases)
    fragment = "\n".join(lines) + "\n"
    note = (
        "# Kimi Code subagent model pool\n\n"
        "Confirm every staged alias is still present in the target Kimi configuration before applying the fragment. "
        "Review and replace the entire `[secondary_model]` section and its nested tables in `config.toml`. "
        "A missing alias can prevent session startup.\n\n"
        "The default applies to subagents spawned without a model choice. A shared effort applies to every "
        "pool member. For per-alias effort defaults, omit `default_effort` and review the target model entries. "
        "Weaver stages the fragment without starting a session or contacting providers.\n\n"
        f"Model snapshot SHA-256: {report['sha256']}\n\n"
        "- https://moonshotai.github.io/kimi-code/en/configuration/config-files.html#secondary_model\n"
    )
    return [
        RenderedArtifact(PurePosixPath("kimi-code/model/secondary-model.fragment.toml"),
                         fragment.encode(), "model-config"),
        RenderedArtifact(PurePosixPath("kimi-code/model/subagent-pool-setup.md"), note.encode(), "model-setup"),
    ]
