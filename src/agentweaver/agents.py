"""Documented, offline custom-agent definitions and explicit manual handoffs.

Rendering never installs an agent, changes a parent's permissions, grants trust,
reads ambient settings, or tests a provider. The caller stages returned artifacts.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters import hermes_profile_mcp, json_bytes
from .artifacts import RenderedArtifact
from .claude_models import inspect_claude_models
from .codex_models import CODEX_EFFORTS, validate_codex_selection
from .config import WeaverError, load_json, safe_https_url, safe_name
from .copilot_models import validate_copilot_selection
from .cursor_models import inspect_cursor_models
from .factory_models import inspect_factory_models
from .integrations import IntegrationSpec, load_integrations
from .models import check_model, hermes_profile_model
from .opencode_models import validate_kilo_selection, validate_opencode_selection
from .qoder_models import inspect_qoder_models
from .skills import selected_portable_skill_ids, stage_hermes_profile_skills

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HOST_RESERVED_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_LLM_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MODEL = re.compile(r"^~?[A-Za-z0-9][A-Za-z0-9._:+@/-]*$")
# Provider-backed routes that Weaver can check against its own catalog.
_QWEN_PROVIDER_ROUTES = {
    "openai": ("openai", "openai-chat"),
    "openai-responses": ("openai", "openai-responses"),
    "anthropic": ("anthropic", "anthropic-messages"),
    "gemini": ("google", "google-generate-content"),
}
_QWEN_AUTH_TYPES = {*_QWEN_PROVIDER_ROUTES, "vertex-ai", "qwen-oauth", "qwen-subscription"}
_MODEL_SECRET = re.compile(
    r"(?:sk-(?:or-|ant-|proj-)?[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{15,}|"
    r"gh[pousr]_[A-Za-z0-9_]{12,}|Bearer\s+[A-Za-z0-9._~-]{12,})", re.IGNORECASE,
)
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}
_STATES = {"native", "manual", "unknown", "unsupported"}
_PATHS = {
    "codex-toml": ".codex/agents/{name}.toml",
    "claude-markdown": ".claude/agents/{name}.md",
    "cline-yaml": ".cline/agents/{name}.yaml",
    "vscode-markdown": ".github/agents/{name}.agent.md",
    "copilot-cli-markdown": ".github/agents/{name}.agent.md",
    "copilot-app-markdown": ".github/agents/{name}.md",
    "factory-markdown": ".factory/droids/{name}.md",
    "opencode-markdown": ".opencode/agents/{name}.md",
    "kilo-markdown": ".kilo/agents/{name}.md",
    "qwen-markdown": ".qwen/agents/{name}.md",
    "kimi-markdown": ".kimi-code/agents/{name}.md",
    "cursor-markdown": ".cursor/agents/{name}.md",
    "kiro-json": ".kiro/agents/{name}.json",
    "qoder-markdown": ".qoder/agents/{name}.md",
    "antigravity-markdown": ".agents/agents/{name}.md",
    "devin-markdown": ".devin/agents/{name}.md",
    "openhands-markdown": ".agents/agents/{name}.md",
}
_ROW_KEYS = {"product_id", "surface_id", "state", "format", "path_template", "model_field", "model_style",
             "effort_field", "efforts", "model_behavior", "reasoning_behavior", "permission_behavior",
             "reserved_names", "evidence"}
_EVIDENCE_KEYS = {"sources", "checked_on", "version_constraint", "fixture_tests", "runtime_checks"}
_STYLES = {"bare", "provider/model", "tier", "local-profile", "display-name-link", "none"}
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_EFFORT_FIELDS = {"codex-toml": "model_reasoning_effort", "claude-markdown": "effort",
                  "factory-markdown": "reasoningEffort", "opencode-markdown": "reasoningEffort",
                  "qoder-markdown": "effort", "vscode-markdown": "reasoning-effort",
                  "copilot-cli-markdown": "reasoning-effort", "kilo-markdown": "variant",
                  "cursor-markdown": "model"}


def _text(value: object, label: str, *, multiline: bool = False, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise WeaverError(f"{label}: non-empty bounded text required")
    text = value.replace("\r\n", "\n")
    for char in text:
        if multiline and char in {"\n", "\t"}:
            continue
        if unicodedata.category(char) in {"Cc", "Cs", "Cf"} or (not multiline and char in {"\n", "\t"}):
            raise WeaverError(f"{label}: control or invalid Unicode character")
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise WeaverError(f"{label}: valid UTF-8 text required") from None
    return text


def _name(value: object, label: str = "Agent name") -> str:
    identifier = _text(value, label, maximum=64)
    if not _ID.fullmatch(identifier) or identifier in _RESERVED:
        raise WeaverError(f"{label}: portable lowercase letters, digits and single hyphens required")
    return identifier


def _llm_profile(value: object) -> str:
    reference = _text(value, "Local LLM profile reference", maximum=69)
    name = reference.removesuffix(".json")
    if (not _LLM_PROFILE.fullmatch(name) or reference == "inherit" or _MODEL_SECRET.search(name)
            or name.casefold().startswith(("sk-", "bearer", "aiza", "ghp_", "gho_", "ghs_", "ghr_",
                                           "github_pat_", "glpat-", "xoxb-", "xoxp-", "xoxa-", "xoxr-"))
            or re.search(r"(?:AKIA|ASIA)[A-Z0-9]{16}", name)):
        raise WeaverError("Invalid local LLM profile reference")
    return reference


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise WeaverError(f"{label}: list required")
    result = [_text(item, label, maximum=2000) for item in value]
    if len(result) != len(set(result)):
        raise WeaverError(f"{label}: duplicate values")
    return result


def _evidence(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _EVIDENCE_KEYS:
        raise WeaverError("Invalid custom-agent evidence")
    sources = _strings(value["sources"], "Agent sources")
    if any(not safe_https_url(source, allow_fragment=True) for source in sources):
        raise WeaverError("Agent sources must be credential-free HTTPS URLs")
    checked = value["checked_on"]
    if checked is not None:
        if not isinstance(checked, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
            raise WeaverError("Invalid custom-agent evidence date")
        try:
            date.fromisoformat(checked)
        except (ValueError, TypeError):
            raise WeaverError("Invalid custom-agent evidence date") from None
    if sources and checked is None:
        raise WeaverError("Custom-agent documentation needs a checked date")
    version = value["version_constraint"]
    if version is not None:
        _text(version, "Agent version constraint")
    references: dict[str, list[str]] = {}
    for key in ("fixture_tests", "runtime_checks"):
        references[key] = _strings(value[key], key)
        for reference in references[key]:
            path = PurePosixPath(reference)
            if path.is_absolute() or ".." in path.parts or "\\" in reference or ":" in reference:
                raise WeaverError("Agent evidence references must be relative paths")
    return {"sources": sources, "checked_on": checked, "version_constraint": version, **references}


def _load_definitions(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    catalog = load_json(Path(root) / "catalogs/custom-agents.json")
    if set(catalog) != {"schema_version", "definitions"} or type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
        raise WeaverError("Unsupported custom-agent catalog")
    rows = catalog["definitions"]
    if not isinstance(rows, list) or not rows:
        raise WeaverError("Custom-agent definitions must be a non-empty list")
    integrations = load_integrations(root)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise WeaverError("Invalid custom-agent definition metadata")
        product = _name(row["product_id"], "Agent product id")
        surface = _name(row["surface_id"], "Agent surface id")
        if product not in integrations or surface not in integrations[product].surface_ids():
            raise WeaverError("Custom-agent definition must reference a registered product surface")
        key = (product, surface)
        if key in result:
            raise WeaverError("Duplicate custom-agent product surface")
        state = row["state"]
        if not isinstance(state, str) or state not in _STATES:
            raise WeaverError("Invalid custom-agent state")
        format_name, path = row["format"], row["path_template"]
        if state == "native":
            if not isinstance(format_name, str) or format_name not in _PATHS or path != _PATHS[format_name]:
                raise WeaverError("Undocumented or unsafe native custom-agent format/path")
            if integrations[product].kind == "provider":
                raise WeaverError("Provider gateways cannot contain native custom-agent definitions")
        elif format_name is not None or path is not None:
            raise WeaverError("Non-native agent route cannot claim a native format or path")
        model_field = "modelId" if format_name == "cline-yaml" else "model"
        if row["model_field"] not in (None, model_field):
            raise WeaverError("Invalid custom-agent model field")
        if row["effort_field"] is not None and row["effort_field"] != _EFFORT_FIELDS.get(format_name):
            raise WeaverError("Undocumented custom-agent effort field")
        if (not isinstance(row["model_style"], str) or row["model_style"] not in _STYLES
                or (row["model_field"] is None) != (row["model_style"] == "none")):
            raise WeaverError("Invalid custom-agent model style")
        if (format_name == "openhands-markdown") != (row["model_style"] == "local-profile"):
            raise WeaverError("Local profile selection requires the documented OpenHands custom-agent format")
        if row["model_style"] == "display-name-link" and (product, surface, format_name) != (
            "qoder", "ide", "qoder-markdown"
        ):
            raise WeaverError("Display name/model links require the documented Qoder IDE agent format")
        efforts = _strings(row["efforts"], "Agent effort choices")
        allowed_efforts = CODEX_EFFORTS if format_name == "codex-toml" else _EFFORTS
        catalog_efforts = (
            format_name in {"vscode-markdown", "kilo-markdown", "cursor-markdown"}
            and row["effort_field"] is not None
        )
        if (set(efforts) - allowed_efforts or (catalog_efforts and efforts)
                or (not catalog_efforts and bool(efforts) != (row["effort_field"] is not None))):
            raise WeaverError("Invalid custom-agent effort declaration")
        if format_name == "factory-markdown" and set(efforts) - {"low", "medium", "high"}:
            raise WeaverError("Factory custom-agent effort is limited to low/medium/high")
        if state != "native" and (row["model_field"] is not None or row["effort_field"] is not None):
            raise WeaverError("Non-native routes cannot apply model or effort fields")
        for field in ("model_behavior", "reasoning_behavior", "permission_behavior"):
            _text(row[field], field, maximum=2000)
        reserved = _strings(row["reserved_names"], "Reserved agent names")
        if any(not _HOST_RESERVED_NAME.fullmatch(item) for item in reserved):
            raise WeaverError("Invalid reserved agent name")
        evidence = _evidence(row["evidence"])
        if state in {"native", "manual"} and not evidence["sources"]:
            raise WeaverError("Documented custom-agent routes require sources")
        result[key] = {**row, "efforts": efforts, "reserved_names": reserved, "evidence": evidence}
    return result


def _unknown_row(product: str, surface: str) -> dict[str, Any]:
    return {"product_id": product, "surface_id": surface, "state": "unknown", "format": None,
            "path_template": None, "model_field": None, "model_style": "none", "effort_field": None,
            "efforts": [], "model_behavior": "No custom-agent model schema established.",
            "reasoning_behavior": "No custom-agent reasoning schema established.",
            "permission_behavior": "No native permission boundary is claimed.", "reserved_names": [],
            "evidence": {"sources": [], "checked_on": None, "version_constraint": None,
                         "fixture_tests": [], "runtime_checks": []}}


def agent_inventory(root: Path) -> dict[str, Any]:
    """List native exporters, manual projections and gaps without runtime claims."""
    definitions = _load_definitions(root)
    integrations = load_integrations(root)
    rows = [definitions.get((product, surface.id), _unknown_row(product, surface.id))
            for product, integration in integrations.items() for surface in integration.surfaces]
    return {"schema_version": 1, "network_used": False, "credentials_read": False,
            "runtime_verified": False, "definitions": rows}


def _model_selection(row: Mapping[str, Any], root: Path, model_id: str | None, effort: str | None,
                     provider_id: str | None, snapshot: Mapping[str, Any] | None, *,
                     protocol: str | None = None) -> tuple[str | None, dict[str, Any] | None]:
    if row["model_style"] == "local-profile":
        if model_id not in {None, "inherit"} or any(value is not None for value in (effort, provider_id, snapshot)):
            raise WeaverError("OpenHands custom agents accept a local LLM profile reference or inheritance; raw model/provider/effort overrides are not exported")
        return None, None
    if row["format"] == "devin-markdown" and model_id == "inherit":
        raise WeaverError("Devin custom subagents omit model for the router/admin default; inherit is not a documented model selector")
    inherited = model_id in {None, "inherit"}
    if row["model_field"] is None and not inherited:
        raise WeaverError("This custom-agent format ignores or does not support an explicit model")
    if effort is not None and (row["effort_field"] is None or effort not in row["efforts"]):
        raise WeaverError("Custom-agent effort field or requested effort is unsupported or unknown")
    if effort is not None and inherited:
        raise WeaverError("Explicit effort requires an explicit model with known reasoning support")
    if inherited:
        if provider_id is not None or snapshot is not None:
            raise WeaverError("Provider validation and snapshots require an explicit model")
        return None, None
    model = _text(model_id, "Agent model id", maximum=250)
    if (not _MODEL.fullmatch(model) or _MODEL_SECRET.search(model)
            or model.casefold().startswith(("/", "sk-", "bearer", "aiza", "ghp_", "gho_", "ghs_", "ghr_"))
            or re.match(r"^[A-Za-z]:/", model)
            or "//" in model or any(part in {"", ".", ".."} for part in model.split("/"))):
        raise WeaverError("Invalid custom-agent model identifier")
    style = row["model_style"]
    if style == "tier":
        if model not in {"flash", "pro"} or effort is not None or provider_id is not None or snapshot is not None:
            raise WeaverError("This host accepts only documented flash/pro tiers, not provider model overrides")
        return model, None
    if snapshot is not None and provider_id is None:
        raise WeaverError("A model snapshot requires its provider id")
    if effort is not None and provider_id is None:
        raise WeaverError("Explicit custom-agent effort requires provider-specific reasoning evidence")
    bare_model = (model.removeprefix(provider_id + "/")
                  if style == "provider/model" and provider_id is not None else model)
    checked = (check_model(root, provider_id, bare_model, snapshot, effort=effort, protocol=protocol)
               if provider_id is not None else None)
    if checked is not None:
        architecture = checked["architecture"]
        outputs = architecture.get("output_modalities") if architecture is not None else None
        if outputs and "text" not in outputs:
            raise WeaverError("Selected model does not declare text output; native language-agent definitions require text output")
    if style == "provider/model":
        if provider_id is not None:
            model = f"{provider_id}/{bare_model}"
        elif "/" not in model:
            raise WeaverError("This custom-agent model must use the documented provider/model format")
    return model, checked


def _quoted(value: str) -> str:
    # JSON basic strings are also YAML scalars and, for validated controls, TOML strings.
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _markdown(fields: Mapping[str, Any], instructions: str) -> bytes:
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False, allow_nan=False)}" for key, value in fields.items())
    return f"---\n{header}\n---\n\n{instructions}\n".encode("utf-8")


def _native(row: Mapping[str, Any], name: str, description: str, instructions: str,
            model: str | list[str] | None, effort: str | None, *, opencode_v2: bool = False,
            skill_ids: tuple[str, ...] = (), copilot_catalog: bool = False,
            cline_provider: str | None = None, copilot_model_policy: str | None = None) -> bytes:
    format_name = row["format"]
    if format_name == "codex-toml":
        fields = {"name": name, "description": description, "developer_instructions": instructions}
        if isinstance(model, str):
            fields["model"] = model
        if effort is not None:
            fields["model_reasoning_effort"] = effort
        lines = [f"{key} = {_quoted(value)}" for key, value in fields.items()]
        lines.extend(["", "[features]", "shell_tool = false", "apps = false", "plugins = false"])
        return ("\n".join(lines) + "\n").encode("utf-8")
    fields_any: dict[str, Any] = {"name": name, "description": description}
    if format_name == "kiro-json":
        fields_any.update(prompt=instructions, tools=["read"], includeMcpJson=False, includePowers=False,
                          mcpServers={}, permissions={"rules": [
                              {"capability": capability, "match": ["*"], "effect": "deny"}
                              for capability in ("fs_write", "shell", "mcp", "subagent")]})
        if model is not None:
            fields_any["model"] = model
        return (json.dumps(fields_any, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if format_name == "factory-markdown":
        fields_any.update(model=model or "inherit", tools="read-only", mcpServers=[])
    elif format_name in {"vscode-markdown", "copilot-app-markdown"}:
        fields_any["tools"] = ["read", "search"]
        if copilot_catalog:
            fields_any["target"] = "github-copilot"
            if copilot_model_policy is not None:
                fields_any["model-policy"] = copilot_model_policy
            if skill_ids:
                fields_any["skills"] = list(skill_ids)
    elif format_name == "copilot-cli-markdown":
        fields_any["tools"] = ["read", "grep", "glob"]
        fields_any["strict-tools-list"] = True
        if copilot_model_policy is not None:
            fields_any["model-policy"] = copilot_model_policy
    elif format_name == "claude-markdown":
        fields_any.update(model=model or "inherit", tools=["Read", "Glob", "Grep"])
    elif format_name == "cline-yaml":
        fields_any["tools"] = ["read_files", "search_codebase"]
        if cline_provider is not None:
            fields_any["providerId"] = cline_provider
        if skill_ids:
            fields_any["skills"] = list(skill_ids)
    elif format_name == "qwen-markdown":
        fields_any.update(model=model or "inherit", tools=["read_file", "grep_search", "glob"])
    elif format_name == "kimi-markdown":
        fields_any.update(tools=["Read", "Grep", "Glob"], subagents=[], override=False)
    elif format_name == "cursor-markdown":
        if row["surface_id"] == "cli":
            # The bundled CLI loader reads literal values after the first colon on each line.
            # The name, model and single-line description have already been validated.
            return (f"---\nname: {name}\ndescription: {description}\nmodel: {model or 'inherit'}\n"
                    f"readonly: true\n---\n\n{instructions}\n").encode("utf-8")
        fields_any.update(model=model or "inherit", readonly=True)
    elif opencode_v2:
        fields_any.pop("name")
        rules = [{"action": "*", "resource": "*", "effect": "deny"}]
        rules.extend({"action": action, "resource": "*", "effect": "allow"} for action in ("read", "glob", "grep"))
        rules.extend({"action": "read", "resource": pattern, "effect": "deny"}
                     for pattern in ("*.env", "*.env.*"))
        rules.extend({"action": "skill", "resource": skill_id, "effect": "allow"} for skill_id in skill_ids)
        fields_any.update(mode="subagent", permissions=rules)
    elif format_name in {"opencode-markdown", "kilo-markdown"}:
        fields_any.pop("name")  # Both hosts derive the identifier from the filename.
        permission = ({"*": "deny", "read": "allow", "glob": "allow", "grep": "allow", "list": "allow"}
                      if format_name == "opencode-markdown" else
                      {"edit": "deny", "bash": "deny", "task": "deny", "webfetch": "deny",
                       "websearch": "deny", "todowrite": "deny", "read": "allow", "glob": "allow", "grep": "allow"})
        fields_any.update(mode="subagent", permission=permission)
    elif format_name == "qoder-markdown":
        fields_any.update(tools=["Read", "Grep", "Glob"], mcpServers=[])
    elif format_name == "devin-markdown":
        fields_any["allowed-tools"] = ["read", "grep", "glob"]
    elif format_name == "openhands-markdown":
        fields_any.update(model="inherit", tools=[])
    elif format_name == "antigravity-markdown":
        fields_any.update(model=model or "inherit", tools=["view_file", "grep_search"], subagent=True,
                          mainAgent=False, commandExecutionPolicy="off", mcpServers=[])
    else:
        raise WeaverError("Native custom-agent renderer is unavailable")
    if model is not None:
        fields_any[row["model_field"]] = model
    if effort is not None:
        fields_any[row["effort_field"]] = effort
    return _markdown(fields_any, instructions)


def render_agent_definition(spec: IntegrationSpec, surface: str, *, root: Path, name: str,
                            description: str, instructions: str, model_id: str | None = None,
                            effort: str | None = None, provider_id: str | None = None,
                            snapshot: Mapping[str, Any] | None = None,
                            llm_profile: str | None = None,
                            codex_catalog: Path | None = None, opencode_catalog: Path | None = None,
                            model_variant: str | None = None,
                            allowed_skills: list[str] | None = None, hermes_profile: bool = False,
                            profile_skills: list[str] | None = None, mcp_profile: str | None = None,
                            profile_mcp: list[str] | None = None,
                            profile_context_size: int | None = None,
                            hermes_profile_route: bool = False, copilot_catalog: Path | None = None,
                            copilot_fallbacks: list[str] | None = None, copilot_model_policy: str | None = None,
                            kilo_catalog: Path | None = None,
                            factory_catalog: Path | None = None,
                            qoder_catalog: Path | None = None,
                            qwen_auth_type: str | None = None,
                            claude_catalog: Path | None = None, cursor_catalog: Path | None = None,
                            cursor_parameters: list[str] | None = None) -> list[RenderedArtifact]:
    """Render a reviewable definition plus limitations, without touching a live host."""
    if not isinstance(spec, IntegrationSpec):
        raise WeaverError("Integration specification required")
    _name(surface, "Agent surface id")
    for label, value in (("model_id", model_id), ("effort", effort), ("provider_id", provider_id)):
        if value is not None:
            _text(value, label, maximum=250)
    if snapshot is not None and not isinstance(snapshot, Mapping):
        raise WeaverError("Custom-agent model snapshot must be normalized model metadata")
    if cursor_parameters and cursor_catalog is None:
        raise WeaverError("Cursor model parameters require --cursor-catalog")
    if cursor_catalog is not None:
        if spec.id != "cursor" or surface not in {"cli", "ide"} or hermes_profile:
            raise WeaverError("A Cursor SDK catalog requires the local Cursor CLI or IDE target")
        if any(value is not None for value in (provider_id, snapshot, llm_profile, codex_catalog, opencode_catalog,
                                               copilot_catalog, kilo_catalog, factory_catalog, qoder_catalog,
                                               claude_catalog)):
            raise WeaverError("A Cursor SDK catalog conflicts with other model discovery options")
    if model_variant is not None and opencode_catalog is None and kilo_catalog is None:
        raise WeaverError("A model variant requires a saved OpenCode V2 or Kilo model catalog")
    qwen_protocol = None
    if qwen_auth_type is not None:
        if spec.id != "qwen-code" or surface not in {"cli", "desktop", "vscode"}:
            raise WeaverError("A Qwen authentication type requires a Qwen Code workspace custom-agent target")
        if qwen_auth_type not in _QWEN_AUTH_TYPES:
            raise WeaverError("Unknown built-in Qwen authentication type")
        if model_id in {None, "inherit", "fast"}:
            raise WeaverError("A Qwen authentication type requires an explicit Qwen model ID")
        if provider_id is not None:
            qwen_route = _QWEN_PROVIDER_ROUTES.get(qwen_auth_type)
            if qwen_route is None or qwen_route[0] != provider_id:
                raise WeaverError("Provider validation requires the matching built-in Qwen authentication type")
            qwen_protocol = qwen_route[1]
    if claude_catalog is not None:
        if spec.id != "claude-code" or surface not in {"cli", "desktop", "vscode"} or hermes_profile:
            raise WeaverError("A Claude Code model catalog requires a local Claude Code custom-agent target")
        if any(value is not None for value in (provider_id, snapshot, llm_profile, codex_catalog, opencode_catalog,
                                               copilot_catalog, kilo_catalog, factory_catalog, qoder_catalog)):
            raise WeaverError("A Claude Code model catalog conflicts with other model discovery options")
    if qoder_catalog is not None:
        if spec.id != "qoder" or surface not in {"cli", "ide"} or hermes_profile:
            raise WeaverError("A Qoder SDK catalog requires a Qoder CLI or IDE custom-agent target")
        if any(value is not None for value in (provider_id, snapshot, llm_profile, codex_catalog,
                                               opencode_catalog, copilot_catalog, kilo_catalog, factory_catalog)):
            raise WeaverError("A Qoder SDK catalog conflicts with other model discovery options")
    if factory_catalog is not None:
        if (spec.id, surface) != ("factory", "cli") or hermes_profile:
            raise WeaverError("A Factory Droid SDK catalog requires a Factory CLI custom-agent target")
        if any(value is not None for value in (provider_id, snapshot, llm_profile, codex_catalog,
                                               opencode_catalog, copilot_catalog, kilo_catalog)):
            raise WeaverError("A Factory Droid SDK catalog conflicts with other model discovery options")
    if kilo_catalog is not None:
        if spec.id != "kilo" or surface not in {"cli", "ide"} or hermes_profile:
            raise WeaverError("A Kilo catalog requires a Kilo CLI or VS Code custom-agent target")
        if any(value is not None for value in (provider_id, snapshot, llm_profile, codex_catalog,
                                               opencode_catalog, copilot_catalog, effort)):
            raise WeaverError("A Kilo catalog conflicts with provider, catalog, profile or effort overrides")
    cline_configured_agent = spec.id == "cline" and surface in {"cli", "desktop"}
    if allowed_skills and opencode_catalog is None and copilot_catalog is None and not cline_configured_agent:
        raise WeaverError("Agent Skill selection requires Cline CLI/Desktop or an OpenCode V2 or Copilot SDK catalog")
    if copilot_fallbacks and copilot_catalog is None:
        raise WeaverError("Copilot fallback models require --copilot-catalog")
    if copilot_model_policy is not None:
        if copilot_catalog is None:
            raise WeaverError("Copilot model policy requires --copilot-catalog")
        if copilot_model_policy not in ("preferred", "required"):
            raise WeaverError("Unknown Copilot model policy")
    if copilot_catalog is not None and (spec.id != "copilot" or surface not in {"vscode", "cli"}):
        raise WeaverError("A Copilot SDK catalog requires a Copilot CLI or VS Code target")
    if (spec.id, surface) == ("copilot", "cli") and allowed_skills:
        raise WeaverError("Stage Copilot CLI Skills separately with bundle")
    if profile_skills and not hermes_profile:
        raise WeaverError("Profile Skill staging requires --hermes-profile")
    if profile_context_size is not None and not hermes_profile:
        raise WeaverError("Profile context size requires --hermes-profile")
    if hermes_profile_route and not hermes_profile:
        raise WeaverError("Hermes profile routing requires --hermes-profile")
    if mcp_profile is not None or profile_mcp:
        if not hermes_profile:
            raise WeaverError("Profile MCP staging requires --hermes-profile")
        if not safe_name(mcp_profile) or not profile_mcp:
            raise WeaverError("Profile MCP staging requires a valid --mcp-profile and at least one --profile-mcp")
    spec.surface(surface)
    registered = load_integrations(root).get(spec.id)
    if registered != spec:
        raise WeaverError("Custom-agent rendering requires the registered integration specification")
    identifier = _name(name)
    summary = _text(description, "Agent description")
    prompt = _text(instructions, "Agent instructions", multiline=True, maximum=131072)
    if hermes_profile:
        if (spec.id, surface) != ("hermes", "cli"):
            raise WeaverError("Hermes profile staging requires the Hermes CLI surface")
        if identifier == "default":
            raise WeaverError("Hermes reserves the default profile name")
        if any(value is not None for value in (llm_profile, codex_catalog, opencode_catalog, model_variant)):
            raise WeaverError("Client-specific model flags are unavailable for Hermes profiles")
        config: dict[str, Any] = {}
        if any(value is not None for value in (model_id, effort, provider_id, snapshot, profile_context_size)):
            if model_id is None or provider_id is None:
                raise WeaverError("Hermes profile model staging requires both --provider and --model")
            config.update(hermes_profile_model(root, provider_id, model_id, snapshot, effort, profile_context_size))
        if mcp_profile is not None and profile_mcp:
            profile = load_json(root / "profiles" / f"{mcp_profile}.json")
            catalog = load_json(root / "catalogs/mcp-servers.json")
            config.update(hermes_profile_mcp(profile, catalog, profile_mcp))
        return _hermes_profile(root, identifier, summary, prompt, profile_skills or [], config,
                               stage_route=hermes_profile_route)
    row = _load_definitions(root).get((spec.id, surface), _unknown_row(spec.id, surface))
    if (spec.id, surface) == ("kiro", "cli") and (
        provider_id is not None or snapshot is not None or (model_id is not None and "/" in model_id)
    ):
        raise WeaverError("Kiro CLI profiles use model IDs from the Kiro model service")
    if identifier in row["reserved_names"]:
        raise WeaverError("Agent name is reserved or would replace a built-in agent; choose a distinct custom name")
    state = row["state"]
    if state in {"unknown", "unsupported"}:
        raise WeaverError(f"Custom-agent definition unavailable for {spec.id}/{surface}: {state}")
    if state == "manual" and any(value is not None for value in (model_id, effort, provider_id, snapshot)):
        raise WeaverError("Manual custom-agent handoff cannot apply or validate native model/effort overrides")
    model: str | None
    checked: dict[str, Any] | None
    codex_digest: str | None = None
    claude_digest: str | None = None
    opencode_digest: str | None = None
    copilot_digest: str | None = None
    kilo_digest: str | None = None
    factory_digest: str | None = None
    qoder_digest: str | None = None
    cursor_digest: str | None = None
    copilot_models: list[str] | None = None
    skill_ids: tuple[str, ...] = ()
    if cursor_catalog is not None:
        if row["format"] != "cursor-markdown" or row["effort_field"] != "model" or model_id in {None, "inherit"}:
            raise WeaverError("A Cursor SDK catalog requires an explicit model for a native Cursor subagent")
        cursor_report = inspect_cursor_models(cursor_catalog, model=model_id, effort=effort,
                                              parameters=cursor_parameters)
        model, checked = cursor_report["selection"]["subagent_selector"], None
        cursor_digest = cursor_report["snapshot_sha256"]
    elif claude_catalog is not None:
        if row["format"] != "claude-markdown" or row["model_field"] != "model" or model_id in {None, "inherit"}:
            raise WeaverError("A Claude Code custom-agent catalog requires an explicit model selector")
        if effort is not None and effort not in row["efforts"]:
            raise WeaverError("Unsupported Claude Code custom-agent effort")
        claude_report = inspect_claude_models(claude_catalog, model=model_id, effort=effort)
        selection = claude_report["selection"]
        model, checked = selection["value"], None
        if model == "default":
            model = selection["resolved_model"]
            if model in {None, "default", "inherit"}:
                raise WeaverError("Claude Code default requires a resolved model in the saved catalog for an agent file")
        claude_digest = claude_report["snapshot_sha256"]
    elif qoder_catalog is not None:
        if row["format"] != "qoder-markdown" or model_id is None or model_id == "inherit":
            raise WeaverError("A Qoder SDK catalog requires an explicit Qoder subagent model")
        if effort is not None and effort not in row["efforts"]:
            raise WeaverError("Unsupported Qoder custom-agent effort")
        model, checked = _model_selection(row, root, model_id, None, None, None)
        qoder_report = inspect_qoder_models(qoder_catalog, model=model_id, effort=effort)
        qoder_digest = qoder_report["snapshot_sha256"]
        if row["model_style"] == "display-name-link":
            label = _text(qoder_report["selection"]["display_name"], "Qoder IDE model display name")
            if label != label.strip() or any(char in label for char in "[]\\"):
                raise WeaverError("Qoder IDE model display name cannot be staged as a model link")
            model = f"[{label}]({model})"
    elif factory_catalog is not None:
        if row["format"] != "factory-markdown" or model_id in {None, "inherit"}:
            raise WeaverError("A Factory Droid SDK catalog requires an explicit custom-droid model")
        if effort is not None and effort not in row["efforts"]:
            raise WeaverError("Factory custom-agent effort is limited to low/medium/high")
        model, checked = _model_selection(row, root, model_id, None, None, None)
        factory_report = inspect_factory_models(factory_catalog, model=model_id, effort=effort)
        factory_digest = factory_report["snapshot_sha256"]
    elif kilo_catalog is not None:
        if row["format"] != "kilo-markdown" or row["effort_field"] != "variant":
            raise WeaverError("Kilo variant staging requires the documented Markdown agent format")
        if model_id is None or model_id == "inherit":
            raise WeaverError("A Kilo catalog requires an explicit provider/model")
        model, kilo_digest = validate_kilo_selection(kilo_catalog, model_id, model_variant)
        checked = None
    elif copilot_catalog is not None:
        if any(value is not None for value in (provider_id, snapshot, llm_profile, codex_catalog, opencode_catalog)):
            raise WeaverError("A Copilot SDK catalog conflicts with other model discovery options")
        if model_id is None or model_id == "inherit":
            raise WeaverError("A Copilot SDK catalog requires an explicit model")
        copilot_models = []
        for choice in [model_id, *(copilot_fallbacks or [])]:
            selection, _ = _model_selection(row, root, choice, None, None, None)
            if selection is None or selection in copilot_models:
                raise WeaverError("Copilot primary and fallback models must be explicit and distinct")
            copilot_models.append(selection)
        copilot_digest = validate_copilot_selection(copilot_catalog, copilot_models, effort)
        if surface == "cli" and effort is not None and effort not in row["efforts"]:
            raise WeaverError("The requested effort is not a documented Copilot CLI setting")
        if allowed_skills:
            skill_ids = selected_portable_skill_ids(root, allowed_skills)
        model, checked = copilot_models[0], None
    elif opencode_catalog is not None:
        if (spec.id, surface, row["format"]) != ("opencode", "cli", "opencode-markdown"):
            raise WeaverError("An OpenCode V2 model catalog requires an OpenCode CLI custom-agent target")
        if any(value is not None for value in (provider_id, snapshot, llm_profile, codex_catalog, effort)):
            raise WeaverError("An OpenCode V2 catalog conflicts with provider, catalog, profile or effort overrides")
        if model_id is None or model_id == "inherit":
            raise WeaverError("An OpenCode V2 model catalog requires an explicit provider/model")
        model, opencode_digest = validate_opencode_selection(opencode_catalog, model_id, model_variant)
        if allowed_skills:
            skill_ids = selected_portable_skill_ids(root, allowed_skills)
        checked = None
    elif codex_catalog is not None:
        if spec.id != "codex" or row["format"] != "codex-toml":
            raise WeaverError("A Codex model catalog requires a native Codex custom-agent target")
        if any(value is not None for value in (provider_id, snapshot, llm_profile)):
            raise WeaverError("A Codex model catalog conflicts with provider, provider catalog or LLM profile")
        if model_id is None or model_id == "inherit":
            raise WeaverError("A Codex model catalog requires an explicit model")
        if effort is not None and effort not in row["efforts"]:
            raise WeaverError("Custom-agent effort field or requested effort is unsupported or unknown")
        model, checked = _model_selection(row, root, model_id, None, None, None)
        codex_digest = validate_codex_selection(codex_catalog, model_id, effort)
    elif llm_profile is not None:
        if (spec.id, surface, row["model_style"]) != ("openhands", "cli", "local-profile"):
            raise WeaverError("Local LLM profile references are supported only for OpenHands CLI custom-agent definitions")
        if any(value is not None for value in (model_id, effort, provider_id, snapshot)):
            raise WeaverError("A local LLM profile reference conflicts with model, provider, effort or catalog overrides")
        model, checked = _llm_profile(llm_profile), None
    else:
        if row["model_style"] == "display-name-link" and model_id not in {None, "inherit"}:
            raise WeaverError("Qoder IDE custom-agent model selection requires --qoder-catalog")
        if effort is not None and row["format"] == "cursor-markdown":
            raise WeaverError("Cursor custom-agent effort requires --cursor-catalog")
        if effort is not None and row["format"] in {"vscode-markdown", "copilot-cli-markdown"}:
            raise WeaverError("Copilot custom-agent effort requires --copilot-catalog")
        if cline_configured_agent and model_id not in {None, "inherit"} and provider_id not in {"openrouter", "anthropic"}:
            raise WeaverError("Explicit Cline agent models require a documented Cline provider: openrouter or anthropic")
        model, checked = _model_selection(row, root, model_id, effort, provider_id, snapshot, protocol=qwen_protocol)
        if cline_configured_agent and allowed_skills:
            skill_ids = selected_portable_skill_ids(root, allowed_skills)
    if qwen_auth_type is not None:
        if row["format"] != "qwen-markdown" or model is None:
            raise WeaverError("A Qwen authentication type requires a native Qwen subagent model")
        auth_prefix, separator, _ = model.partition(":")
        if separator and auth_prefix in _QWEN_AUTH_TYPES:
            raise WeaverError("Supply the Qwen model ID separately from its authentication type")
        if qwen_auth_type in {"gemini", "vertex-ai"}:
            model = model.removeprefix("models/")
        if not model:
            raise WeaverError("A Qwen authentication type requires a non-empty model ID")
        model = f"{qwen_auth_type}:{model}"
    permission_behavior = (row["permission_behavior"] if opencode_digest is None else
                           "V2 ordered rules deny all actions first, allow read/glob/grep, and deny reads of "
                           "*.env and *.env.*. Review effective policy before use.")
    lines = ["# Custom agent definition handoff", "", f"Product: {spec.name} ({spec.id})",
             f"Surface: {surface}", f"Agent: {identifier}", f"Description: {summary}",
             f"Export route: {state}", "", "This is an offline staged artifact. Review and import it manually. "
             "No agent was installed or executed; no project trust, credentials, services, or production writes were enabled.",
             "", "Model behavior: " + row["model_behavior"],
             "Reasoning behavior: " + row["reasoning_behavior"],
             "Permission behavior: " + permission_behavior,
             "Tool filters and prompt instructions do not establish an OS sandbox. Parent policy, managed settings, "
             "and the host's installed version still apply. This file grants no new authorization.",
             "Model and reasoning defaults follow the selected host behavior described above. "
             "No maximum reasoning is selected automatically."]
    if row["format"] == "copilot-app-markdown":
        lines.extend(["", "Use the GitHub Copilot app agent picker to select this profile in the local repository. "
                      "Avoid a duplicate profile with the same base filename. Choose the session model and "
                      "available effort beneath the prompt. This export covers local repository sessions."])
    if row["format"] == "devin-markdown":
        lines.extend(["", "Devin Desktop must use the Devin Local agent, with Subagents (Preview) enabled "
                      "through Devin Settings by the operator. Managed access and workspace trust still apply. "
                      "This export does not change those settings. Custom subagents are experimental.",
                      "Omitting model selects the configured default subagent model through a user override, "
                      "the router or an admin-pinned default; it does not request the parent model. "
                      "No inherit token or reasoning field is emitted.",
                      "The profile's allowed-tools is a true tool restriction. The same field on an inline "
                      "Skill has different auto-approval semantics and must not be substituted for this profile."])
    if row["format"] == "openhands-markdown":
        lines.extend(["", "The audited OpenHands CLI development source identifies itself as 1.16.0 with "
                      "SDK 1.28.1. The published CLI 1.16.0 Linux binary bundles SDK 1.21.0. "
                      "See the coverage report for versioned runtime checks.",
                      "Project discovery checks .agents/agents/*.md before .openhands/agents/*.md, then the "
                      "same directories under the user's home. Only top-level Markdown files are loaded; "
                      "readme.md is skipped. Existing registrations, including the CLI's built-in agents and "
                      "plugin agents, can take precedence; use a unique name.",
                      "By default the definition inherits the parent model. A non-inherit OpenHands model field selects "
                      "a named local LLM profile, not a provider model ID. This exporter does not inspect "
                      "profiles or apply raw model, provider or reasoning overrides. An explicitly selected "
                      "profile reference is recorded without reading its configuration or credentials, checking "
                      "its existence, or creating a profile. The host resolves it during later manual use; "
                      "profile availability and reasoning behavior remain unknown.",
                      "tools: [] declares no external tools. Base finish/think behavior may remain, and this "
                      "does not establish an OS sandbox. No MCP servers, hooks or permission modes are emitted. "
                      "Host and parent policy still apply."])
    if (spec.id, surface) == ("openhands", "canvas"):
        lines.extend(["", "## Manual Agent Canvas profile", "",
                      "On the intended backend, open Settings > Agent and create a named OpenHands Agent Profile. "
                      "Set System prompt to Default + your instructions and paste the role below.",
                      "Choose a saved LLM profile and review inherited tools, secrets, and MCP access. "
                      "An unset MCP server selection lets the agent use all configured servers; select only "
                      "the servers intended for this profile.",
                      "Select the saved profile before starting a new conversation."])
    if spec.id == "jetbrains-air" and surface == "ide" and state == "manual":
        lines.extend(["", "## Manual Air IDE preset", "",
                      "Open Manage Agents > Manage Presets, add a New Preset, and choose an already "
                      "installed agent. Set the preset Name, then expand Advanced and review the user "
                      "supplied instructions below for Injected instructions. Save with OK; the preset "
                      "applies to subsequent sessions, not sessions already running.",
                      "Model and Effort choices depend on the selected agent. Default Model and Default "
                      "preserve that agent's own choices. Mode and Plan mode are also agent-dependent; "
                      "this handoff changes none of those controls or authorizations.",
                      "A preset is not a registered subagent file. User-level .jetbrains/acp.json "
                      "registers external ACP executables separately; no registration or executable "
                      "is generated or launched here. The desktop app's .air/mcp.json is not an IDE "
                      "configuration path."])
    if spec.id == "perplexity" and surface == "agent-api" and state == "manual":
        lines.extend(["", "## Manual Agent API Profile", "",
                      "In the API Portal, open Profiles > Create profile. Set the model, system instructions, "
                      "tools, Skills, managed connectors and loop budget, then save the Profile and copy its ID. "
                      "An authorized project admin manages saved resources. Paste the user supplied role below "
                      "into the Profile's instructions; this handoff is not a complete Profile export or upload schema.",
                      "To reference a saved version, the documented POST /v1/agent request contains the following "
                      "reference object. Replace both placeholders using the saved Profile; this is an example "
                      "for manual review and is never submitted by Weaver:",
                      "```json", json.dumps({"profile": {"type": "custom", "id": "profile_REPLACE_WITH_YOUR_PROFILE_ID",
                                                         "version": "REPLACE_WITH_SAVED_VERSION"},
                                              "input": "REPLACE_WITH_YOUR_TASK"}, indent=2), "```",
                      "A request uses either profile or effort, not both. Omitting version or choosing latest "
                      "tracks the latest saved version. Request fields override Profile defaults; tools merge "
                      "per tool instead of replacing the complete tool set, and a connector with the same ID "
                      "replaces that Profile connector. An empty request tools list does not revoke ambient "
                      "Profile access. A Profile is not an authorization boundary; tool approvals and connected "
                      "account permissions still apply. No connectors, tools, approvals or services are enabled here."])
    if checked is not None and cline_configured_agent:
        lines.extend(["", f"Explicit Cline provider: {checked['provider_id']}",
                      "The provider override applies to this configured agent. Review the host's configured "
                      "provider authentication and model access before use."])
    elif checked is not None:
        lines.extend(["", f"Model validation provider: {checked['provider_id']}",
                      "Provider validation does not change the parent's provider or prove availability, entitlement, "
                      "successful inference, or installed-host compatibility."])
    if row["format"] == "qwen-markdown":
        lines.extend(["", "A qualified model selector uses the named Qwen provider configured in the target profile. "
                      "A bare ID is resolved using Qwen's provider lookup. The fast selector uses fastModel "
                      "and falls back to the parent model when that setting cannot be resolved.",
                      "Review the destination and its credentials before using the agent. Configure model-dependent "
                      "reasoning through the selected Qwen provider entry or the session controls."])
    if cursor_digest is not None:
        lines.extend(["", f"Saved Cursor SDK models.list() SHA-256: {cursor_digest}",
                      "The selected options are encoded in the subagent's model string. Confirm access in "
                      "the destination account and team. Cursor can fall back when the configured model is unavailable."])
    if claude_digest is not None:
        if model_id == "default":
            lines.extend(["", "The authored default selector pins the model resolved by the saved session. "
                          "Regenerate with a fresh snapshot to change that pin."])
        lines.extend(["", f"Saved Claude Code ModelInfo SHA-256: {claude_digest}",
                      "Review model and effort in the target session. The saved list describes its source session."])
    if codex_digest is not None:
        lines.extend(["", f"Saved Codex model/list snapshot SHA-256: {codex_digest}",
                      "This selection matches a picker-visible model in the caller-supplied Codex catalog. "
                      "The snapshot may be partial or stale. It does not prove account access, inference, or "
                      "compatibility with the target client. The parent's provider is unchanged."])
    if opencode_digest is not None:
        lines.extend(["", f"Saved OpenCode V2 GET /api/model response SHA-256: {opencode_digest}",
                      "This definition uses V2 model selectors and ordered permissions. Review the effective "
                      "policy and model availability in your OpenCode project before use. The saved catalog "
                      "describes its source project at capture time. No model request was made."])
        if skill_ids:
            lines.extend(["", "Allowed OpenCode Skill IDs: " + ", ".join(skill_ids) + ".",
                          "Stage matching files separately with bundle --skill for each ID. "
                          "Review which source wins for each ID in OpenCode before use. "
                          "A Skill rule grants no additional tool permissions."])
    if copilot_digest is not None:
        lines.extend(["", f"Saved Copilot SDK listModels() SHA-256: {copilot_digest}",
                      "Review exact runtime model IDs in the intended Copilot picker. "
                      "The saved list cannot establish current model access or prove successful inference."])
        if surface == "vscode" and copilot_models and len(copilot_models) > 1:
            lines.append("The editor interprets the ordered model list. Bundled Copilot runtime 1.0.73 "
                         "requires a scalar model when loading project agent files directly.")
        if surface == "vscode" and copilot_model_policy == "required":
            lines.append("VS Code restricts the model picker for this agent and blocks sending when none of "
                         "its models are available. Effective effort can still be changed in the picker. "
                         "The policy applies to the editor-selected agent.")
        if surface == "cli":
            lines.append("This file uses the documented standalone Copilot CLI fields. "
                         "Confirm how the installed version resolves them and review account model access.")
        if skill_ids:
            lines.extend(["", "Copilot Skills to load: " + ", ".join(skill_ids) + ".",
                          "Stage matching files separately with bundle --skill for each ID."])
    if kilo_digest is not None:
        lines.extend(["", f"Saved Kilo GET /api/model response SHA-256: {kilo_digest}",
                      "Kilo resolves the provider, model and named variant in the target project. "
                      "Review provider access and effective settings there. This snapshot does not "
                      "verify the installed client or inference."])
        if model_variant is not None:
            lines.append(f"Explicit Kilo model variant: {model_variant}")
    if factory_digest is not None:
        lines.extend(["", f"Saved Factory Droid SDK listModels() SHA-256: {factory_digest}",
                      "Verify the selected model in the target project and account before use. "
                      "This catalog snapshot does not prove current availability or inference."])
    if qoder_digest is not None:
        lines.extend(["", f"Saved Qoder Agent SDK model catalog SHA-256: {qoder_digest}",
                      "Verify the selection in the target Qoder project and account before use."])
    if cline_configured_agent and skill_ids:
        lines.extend(["", "Cline Skill IDs: " + ", ".join(skill_ids) + ".",
                      f"Stage matching files with bundle --agent cline --surface {surface} and each --skill ID."])
    if cline_configured_agent:
        lines.extend(["", "Review each delegated task before approving it. Cline does not prompt again for "
                      "the child's individual tool calls. The configured agent uses built-in tools and "
                      "the explicitly selected Skills."])
    if llm_profile is not None:
        lines.append(f"Explicit local LLM profile reference: {model}")
    elif model is not None:
        lines.append(f"Explicit native model selection: {model}")
    if effort is not None:
        lines.append(f"Explicit native effort selection: {effort}")
    evidence = row["evidence"]
    lines.extend(["", "Evidence: documentation and offline fixtures are separate from runtime verification.",
                  f"Documentation checked: {evidence['checked_on'] or 'unknown'}",
                  "Runtime checks: " + (", ".join(evidence["runtime_checks"]) or "none recorded"),
                  "Sources:", *[f"- {source}" for source in evidence["sources"]]])
    artifacts: list[RenderedArtifact] = []
    if state == "native":
        path = row["path_template"].replace("{name}", identifier)
        native_model = copilot_models if copilot_models and len(copilot_models) > 1 else model
        native_effort = model_variant if kilo_digest is not None else effort
        if cursor_digest is not None:
            native_effort = None
        content = _native(row, identifier, summary, prompt, native_model, native_effort,
                          opencode_v2=opencode_digest is not None, skill_ids=skill_ids,
                          copilot_catalog=copilot_digest is not None,
                          copilot_model_policy=copilot_model_policy,
                          cline_provider=provider_id if cline_configured_agent else None)
        artifacts.append(RenderedArtifact(PurePosixPath(spec.id, path), content, "agent-definition"))
        lines.extend(["", f"Native project path: {path}"])
    else:
        lines.extend(["", "No native definition path is established for this route. Apply the role through the "
                      "documented host workflow. This handoff is not a discoverable subagent file.",
                      "", "## User supplied instructions", "", prompt])
    artifacts.append(RenderedArtifact(PurePosixPath(spec.id, "agents", identifier, "HANDOFF.md"),
                                      ("\n".join(lines) + "\n").encode("utf-8"), "agent-handoff"))
    return artifacts


def _hermes_profile(root: Path, name: str, description: str, instructions: str,
                    skills: list[str], config: dict[str, Any], *, stage_route: bool) -> list[RenderedArtifact]:
    prefix = PurePosixPath("hermes", "profiles", name)
    handoff_dir = PurePosixPath("hermes", "agents", name)
    manifest = f"name: {json.dumps(name)}\ndescription: {json.dumps(description, ensure_ascii=False)}\n"
    handoff = (
        f"# Hermes profile distribution for {name}\n\n"
        "Review the staged manifest, SOUL.md, selected Skill files and any config.yaml before using "
        "the local-directory workflow documented by Hermes. Choose a new profile name.\n\n"
        "A separate Hermes profile isolates state but does not sandbox its tools. "
        "Installed-client loading and inference remain unverified.\n\n"
        "https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions\n"
    )
    artifacts = [
        RenderedArtifact(prefix / "distribution.yaml", manifest.encode("utf-8"), "profile-manifest"),
        RenderedArtifact(prefix / "SOUL.md", (instructions + "\n").encode("utf-8"), "agent-definition"),
    ]
    if skills:
        artifacts.extend(stage_hermes_profile_skills(root, skills, prefix))
    if config:
        kind = "profile-config" if "model" in config else "mcp-fragment"
        artifacts.append(RenderedArtifact(prefix / "config.yaml", json_bytes(config), kind))
        handoff += "\nHermes preserves local config.yaml on profile updates by default.\n"
    if "model" in config:
        handoff += (
            "\nReview provider authentication, auxiliary routes and fallbacks before starting Hermes. "
            "Review model-specific reasoning support for the selected provider.\n\n"
            "https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models\n"
        )
    if "mcp_servers" in config:
        handoff += (
            "\nEvery staged MCP server is disabled. Review its endpoint or executable, credentials "
            "and allowed tools before enabling it explicitly in the installed profile.\n\n"
            "https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp\n"
        )
    if stage_route:
        fragment = {"delegation": {
            "backend": "profile_routes",
            "backends": {"profile_routes": {"profiles": [{"name": name, "description": description}]}},
        }}
        artifacts.append(RenderedArtifact(handoff_dir / "parent-delegation.fragment.yaml", json_bytes(fragment),
                                          "delegation-fragment"))
        handoff += (
            "\nThe parent fragment requires Hermes's advanced-delegation plugin. After the child profile "
            "has been installed under the listed name, review its credentials and terminal policy and "
            "merge the profile entry into the intended parent's config.yaml. Preserve existing entries and "
            "resolve any same-name entry before merging. Selecting profile_routes changes routing for the "
            "parent's delegate_task calls on new sessions. Review that backend change separately.\n\n"
            "https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation#delegation-strategies-delegationbackend\n"
        )
    artifacts.append(RenderedArtifact(handoff_dir / "PROFILE-HANDOFF.md", handoff.encode("utf-8"), "agent-handoff"))
    return artifacts
