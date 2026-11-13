"""Inspect and validate Copilot selections against a saved SDK model list."""

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import _json_artifact, _load_bounded_snapshot, _model_id, _strings, _text

COPILOT_CLI_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
COPILOT_CLI_EFFORT_FLAGS = COPILOT_CLI_EFFORTS - {"none", "minimal"}
COPILOT_PROVIDERS_DOC = "https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference#providersjson"


def _model_limits(value: object) -> tuple[dict[str, int | None], bool | None]:
    limits: dict[str, int | None] = {}
    if value is None:
        return limits, None
    if not isinstance(value, dict):
        raise WeaverError("Invalid Copilot model capabilities")
    supports = value.get("supports", {})
    raw_limits = value.get("limits", {})
    if not isinstance(supports, dict) or not isinstance(raw_limits, dict):
        raise WeaverError("Invalid Copilot model capabilities")
    supports_effort = supports.get("reasoningEffort")
    if supports_effort is not None and type(supports_effort) is not bool:
        raise WeaverError("Invalid Copilot reasoning support flag")
    for key in ("max_context_window_tokens", "max_prompt_tokens", "max_output_tokens"):
        limit = raw_limits.get(key)
        if limit is not None and (type(limit) is not int or limit < 0):
            raise WeaverError("Invalid Copilot token limit")
        limits[key] = limit or None
    return limits, supports_effort


def inspect_copilot_models(
    path: Path, *, models: list[str] | None = None, effort: str | None = None,
) -> dict[str, Any]:
    """Report saved SDK listModels() rows and optionally check ordered model choices."""
    listing, digest = _load_bounded_snapshot(path, "Copilot SDK model catalog")
    if not isinstance(listing, list):
        raise WeaverError("Expected a Copilot SDK listModels() JSON array")
    candidates: dict[str, dict[str, Any]] = {}
    for entry in listing:
        if not isinstance(entry, dict):
            raise WeaverError("Invalid Copilot model entry")
        identifier = _model_id(entry.get("id"))
        if identifier in candidates:
            raise WeaverError("Duplicate Copilot model ID")
        choices = entry.get("supportedReasoningEfforts")
        efforts = None if choices is None else _strings(choices, "Copilot efforts")
        default = entry.get("defaultReasoningEffort")
        if default is not None:
            default = _text(default, "Copilot default effort")
        limits, supports_effort = _model_limits(entry.get("capabilities"))
        policy = entry.get("policy")
        policy_state = None
        if policy is not None:
            if not isinstance(policy, dict) or policy.get("state") not in ("enabled", "disabled", "unconfigured"):
                raise WeaverError("Invalid Copilot model policy state")
            policy_state = policy["state"]
        candidates[identifier] = {
            "id": identifier, "policy_state": policy_state, "reasoning_effort_supported": supports_effort,
            "supported_reasoning_efforts": efforts, "default_reasoning_effort": default,
            "default_effort_in_list": None if default is None or efforts is None else default in efforts,
            "limits": limits,
        }
    selection = [_model_id(model) for model in models or []]
    if len(selection) != len(set(selection)):
        raise WeaverError("Duplicate Copilot model selection")
    if effort is not None:
        if not selection:
            raise WeaverError("Copilot effort requires an explicit model")
        effort = _text(effort, "Copilot effort")
    for model in selection:
        candidate = candidates.get(model)
        if candidate is None or candidate["policy_state"] in ("disabled", "unconfigured"):
            raise WeaverError("Selected model is missing or unavailable in the saved Copilot catalog")
        if effort is not None and (candidate["reasoning_effort_supported"] is False
                                   or effort not in (candidate["supported_reasoning_efforts"] or [])):
            raise WeaverError("Copilot effort is unsupported or unknown for a selected model")
    return {
        "schema_version": 1, "source": "caller-supplied Copilot SDK listModels() array",
        "snapshot_sha256": digest, "snapshot_completeness": "unknown",
        "models": list(candidates.values()), "selected_models": selection, "selected_reasoning_effort": effort,
        "network_used": False, "credentials_read": False, "inference_tested": False,
    }


def validate_copilot_selection(path: Path, models: list[str], effort: str | None) -> str:
    """Check selections against the same snapshot format used by the inspector."""
    digest: str = inspect_copilot_models(path, models=models, effort=effort)["snapshot_sha256"]
    return digest


def render_copilot_cli_model(path: Path, model: str, effort: str | None) -> list[RenderedArtifact]:
    """Stage the repository model/effort settings for a documented CLI model."""
    if effort is not None and effort not in COPILOT_CLI_EFFORTS:
        raise WeaverError("The requested effort is not a documented Copilot CLI setting")
    report = inspect_copilot_models(path, models=[model], effort=effort)
    settings = {"model": report["selected_models"][0]}
    if effort is not None:
        settings["effortLevel"] = effort
    handoff = (
        "# Copilot CLI repository model defaults\n\n"
        "Review and merge the selected fields into .github/copilot/settings.json. "
        "Copilot applies these overrides in a trusted workspace. Review your CLI version, runtime "
        "model access and higher-priority settings before use.\n\n"
        f"Saved Copilot SDK listModels() SHA-256: {report['snapshot_sha256']}\n\n"
        "- https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference\n"
    )
    return [
        _json_artifact("copilot/.github/copilot/settings.json", settings, "model-config"),
        RenderedArtifact(PurePosixPath("copilot/model/model-setup.md"), handoff.encode(), "model-setup"),
    ]


def copilot_cli_provider_fragment(provider: str, model: str, base_url: str, *, wire_api: str,
                                  efforts: list[str] | None = None, context_size: int | None = None,
                                  tool_calls: bool = False) -> tuple[dict[str, Any], str]:
    """Render a provider entry and return its unambiguous CLI selector."""
    provider_name = f"weaver-{provider}"
    if wire_api == "responses":
        provider_name += "-responses"
    # Keep the CLI selector to two segments and preserve the exact wire ID independently.
    alias = "model-" + hashlib.sha256(model.encode("utf-8")).hexdigest()[:16]
    entry: dict[str, Any] = {"id": alias, "provider": provider_name, "wireModel": model}
    if context_size is not None:
        entry["maxContextWindowTokens"] = context_size
    supports: dict[str, Any] = {}
    if tool_calls:
        supports["toolCalls"] = True
    if efforts:
        supports["reasoning_effort"] = [level for level in efforts if level in COPILOT_CLI_EFFORTS]
    if supports:
        entry["capabilities"] = {"supports": supports}
    document = {"providers": [{"name": provider_name, "type": "openai", "baseUrl": base_url, "wireApi": wire_api}],
                "models": [entry]}
    return document, f"{provider_name}/{alias}"
