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

from .artifacts import RenderedArtifact
from .config import WeaverError, load_json, safe_https_url
from .integrations import IntegrationSpec, load_integrations
from .models import check_model

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HOST_RESERVED_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_MODEL = re.compile(r"^~?[A-Za-z0-9][A-Za-z0-9._:+@/-]*$")
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
    "vscode-markdown": ".github/agents/{name}.agent.md",
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
}
_ROW_KEYS = {"product_id", "surface_id", "state", "format", "path_template", "model_field", "model_style",
             "effort_field", "efforts", "model_behavior", "reasoning_behavior", "permission_behavior",
             "reserved_names", "evidence"}
_EVIDENCE_KEYS = {"sources", "checked_on", "version_constraint", "fixture_tests", "runtime_checks"}
_STYLES = {"bare", "provider/model", "tier", "none"}
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_EFFORT_FIELDS = {"codex-toml": "model_reasoning_effort", "claude-markdown": "effort",
                  "factory-markdown": "reasoningEffort", "opencode-markdown": "reasoningEffort",
                  "qoder-markdown": "effort"}


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
        if row["model_field"] not in (None, "model"):
            raise WeaverError("Invalid custom-agent model field")
        if row["effort_field"] is not None and row["effort_field"] != _EFFORT_FIELDS.get(format_name):
            raise WeaverError("Undocumented custom-agent effort field")
        if (not isinstance(row["model_style"], str) or row["model_style"] not in _STYLES
                or (row["model_field"] is None) != (row["model_style"] == "none")):
            raise WeaverError("Invalid custom-agent model style")
        efforts = _strings(row["efforts"], "Agent effort choices")
        if set(efforts) - _EFFORTS or bool(efforts) != (row["effort_field"] is not None):
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
                     provider_id: str | None, snapshot: Mapping[str, Any] | None) -> tuple[str | None, dict[str, Any] | None]:
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
    checked = check_model(root, provider_id, bare_model, snapshot, effort=effort) if provider_id is not None else None
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
            model: str | None, effort: str | None) -> bytes:
    format_name = row["format"]
    if format_name == "codex-toml":
        fields = {"name": name, "description": description, "developer_instructions": instructions}
        if model is not None:
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
    elif format_name == "vscode-markdown":
        fields_any["tools"] = ["read", "search"]
    elif format_name == "claude-markdown":
        fields_any.update(model=model or "inherit", tools=["Read", "Glob", "Grep"])
    elif format_name == "qwen-markdown":
        fields_any.update(model=model or "inherit", tools=["read_file", "grep_search", "glob"])
    elif format_name == "kimi-markdown":
        fields_any.update(tools=["Read", "Grep", "Glob"], subagents=[], override=False)
    elif format_name == "cursor-markdown":
        fields_any.update(model=model or "inherit", readonly=True)
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
                            snapshot: Mapping[str, Any] | None = None) -> list[RenderedArtifact]:
    """Render a reviewable definition plus limitations, without touching a live host."""
    if not isinstance(spec, IntegrationSpec):
        raise WeaverError("Integration specification required")
    _name(surface, "Agent surface id")
    for label, value in (("model_id", model_id), ("effort", effort), ("provider_id", provider_id)):
        if value is not None:
            _text(value, label, maximum=250)
    if snapshot is not None and not isinstance(snapshot, Mapping):
        raise WeaverError("Custom-agent model snapshot must be normalized model metadata")
    spec.surface(surface)
    registered = load_integrations(root).get(spec.id)
    if registered != spec:
        raise WeaverError("Custom-agent rendering requires the registered integration specification")
    identifier = _name(name)
    summary = _text(description, "Agent description")
    prompt = _text(instructions, "Agent instructions", multiline=True, maximum=131072)
    row = _load_definitions(root).get((spec.id, surface), _unknown_row(spec.id, surface))
    if identifier in row["reserved_names"]:
        raise WeaverError("Agent name would replace a built-in agent; choose a distinct custom name")
    state = row["state"]
    if state in {"unknown", "unsupported"}:
        raise WeaverError(f"Custom-agent definition unavailable for {spec.id}/{surface}: {state}")
    if state == "manual" and any(value is not None for value in (model_id, effort, provider_id, snapshot)):
        raise WeaverError("Manual custom-agent handoff cannot apply or validate native model/effort overrides")
    model, checked = _model_selection(row, root, model_id, effort, provider_id, snapshot)
    lines = ["# Custom agent definition handoff", "", f"Product: {spec.name} ({spec.id})",
             f"Surface: {surface}", f"Agent: {identifier}", f"Description: {summary}",
             f"Export route: {state}", "", "This is an offline staged artifact. Review and import it manually. "
             "No agent was installed or executed; no project trust, credentials, services, or production writes were enabled.",
             "", "Model behavior: " + row["model_behavior"],
             "Reasoning behavior: " + row["reasoning_behavior"],
             "Permission behavior: " + row["permission_behavior"],
             "Tool filters and prompt instructions do not establish an OS sandbox. Parent policy, managed settings, "
             "and the host's installed version still apply. This file grants no new authorization.",
             "Model and reasoning defaults follow the selected host behavior described above. "
             "No maximum reasoning is selected automatically."]
    if row["format"] == "devin-markdown":
        lines.extend(["", "Devin Desktop must use the Devin Local agent, with Subagents (Preview) enabled "
                      "through Devin Settings by the operator. Managed access and workspace trust still apply. "
                      "This export does not change those settings. Custom subagents are experimental.",
                      "Omitting model selects the configured default subagent model through a user override, "
                      "the router or an admin-pinned default; it does not request the parent model. "
                      "No inherit token or reasoning field is emitted.",
                      "The profile's allowed-tools is a true tool restriction. The same field on an inline "
                      "Skill has different auto-approval semantics and must not be substituted for this profile."])
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
    if checked is not None:
        lines.extend(["", f"Model validation provider: {checked['provider_id']}",
                      "Provider validation does not change the parent's provider or prove availability, entitlement, "
                      "successful inference, or installed-host compatibility."])
    if model is not None:
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
        artifacts.append(RenderedArtifact(PurePosixPath(spec.id, path),
                                          _native(row, identifier, summary, prompt, model, effort), "agent-definition"))
        lines.extend(["", f"Native project path: {path}"])
    else:
        lines.extend(["", "No native definition path is established for this route. Apply the role through the "
                      "documented host workflow. This handoff is not a discoverable subagent file.",
                      "", "## User supplied instructions", "", prompt])
    artifacts.append(RenderedArtifact(PurePosixPath(spec.id, "agents", identifier, "HANDOFF.md"),
                                      ("\n".join(lines) + "\n").encode("utf-8"), "agent-handoff"))
    return artifacts
