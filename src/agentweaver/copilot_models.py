"""Check VS Code Copilot model selections against a saved SDK model list."""

from pathlib import Path

from .config import WeaverError
from .models import _load_bounded_snapshot, _model_id, _text


def validate_copilot_selection(path: Path, models: list[str], effort: str | None) -> str:
    """Read a saved SDK listModels() array and return its content hash."""
    listing, digest = _load_bounded_snapshot(path, "Copilot SDK model catalog")
    if not isinstance(listing, list):
        raise WeaverError("Expected a Copilot SDK listModels() JSON array")
    candidates: dict[str, set[str]] = {}
    seen: set[str] = set()
    for entry in listing:
        if not isinstance(entry, dict):
            raise WeaverError("Invalid Copilot model entry")
        identifier = _model_id(entry.get("id"))
        if identifier in seen:
            raise WeaverError("Duplicate Copilot model ID")
        seen.add(identifier)
        choices = entry.get("supportedReasoningEfforts", [])
        if not isinstance(choices, list):
            raise WeaverError("Invalid Copilot model effort list")
        efforts = {_text(choice, "Copilot effort") for choice in choices}
        if len(efforts) != len(choices):
            raise WeaverError("Duplicate Copilot model effort")
        policy = entry.get("policy")
        if policy is not None:
            if not isinstance(policy, dict) or policy.get("state") not in ("enabled", "disabled", "unconfigured"):
                raise WeaverError("Invalid Copilot model policy state")
            if policy["state"] != "enabled":
                continue
        candidates[identifier] = efforts
    for model in models:
        if model not in candidates:
            raise WeaverError("Selected model is missing or unavailable in the saved Copilot catalog")
        if effort is not None and effort not in candidates[model]:
            raise WeaverError("Copilot effort is unsupported or unknown for a selected model")
    return digest
