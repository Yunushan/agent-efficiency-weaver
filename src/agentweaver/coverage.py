"""Offline requested-scope mapping, separate from capability and runtime support."""
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .config import WeaverError, load_json
from .integrations import (
    CAPABILITY_NAMES,
    OS_IDS,
    IntegrationSpec,
    _canonical_id,
    _strict_object,
    _string,
    _string_list,
    load_integrations,
)

SCHEMA_VERSION = 1
LIMITATIONS = (
    "Requested-scope mapping records which product surfaces were considered; it does not establish support.",
    "Documentation evidence, fixture tests and recorded runtime checks are separate.",
    "Unknown capabilities and missing evidence remain unresolved; a mapping is not a native exporter.",
    "Model IDs and reasoning controls depend on the selected product, surface, provider and model.",
    "Recorded observations apply to the stated version, execution surface, environment and check.",
    "Coverage does not run clients, check account entitlement or verify exhaustive model availability.",
)

_OBSERVATION_CHECKS = {
    "skill-discovery": ("skills", "The client listed the enabled generated project Skill."),
    "agent-discovery": ("agents", "The runtime discovered the generated project agent and loaded its prompt."),
    "agent-load": ("agents", "A spawned custom agent received its generated developer instructions."),
    "stdio-tool-call": ("mcp", "The parent session called a local stdio fixture from the generated configuration."),
    "stdio-subagent-approved-tool-call": ("mcp", "The subagent called the fixture with explicit local tool approval."),
    "remote-config-parse": ("mcp", "The client parsed the remote MCP entry without opening a connection."),
    "public-docs-https-tool-call": (
        "mcp", "The signed-out client discovered the public OpenAI Docs tools and called the documentation list directly.",
    ),
    "public-docs-https-subagent-tool-call": (
        "mcp", "A generated subagent inherited the project's public OpenAI Docs server and called its documentation list "
        "over HTTPS without an approval override. The signed-out probe used the host TLS root and scripted model responses.",
    ),
    "context7-https-subagent-tool-call": (
        "mcp", "A generated subagent inherited the project's Context7 server and resolved a public library over HTTPS. "
        "The signed-out probe supplied the host TLS root to the isolated process and scripted model responses locally.",
    ),
    "workspace-mcp-parse": ("mcp", "The native workspace parser accepted generated HTTP and stdio entries."),
    "workspace-stdio-tool-call": (
        "mcp", "The bundled runtime loaded the trusted workspace file, discovered the local tool, and called it directly.",
    ),
    "model-catalog": ("models", "The client listed model IDs and advertised reasoning efforts across its catalog pages."),
    "agent-model-effort-selection": ("reasoning", "The selected child model and effort reached a scripted local endpoint."),
    "agent-model-effort-config-load": (
        "reasoning", "The direct runtime loader read the scalar model and effort into the selected agent definition.",
    ),
    "agent-model-array-rejected": (
        "models", "The direct runtime loader rejected the generated fallback array because it expected a scalar model.",
    ),
    "named-profile-model-request": (
        "models", "The CLI sent the generated named profile's model to a scripted loopback Responses endpoint.",
    ),
    "named-profile-effort-precedence": (
        "reasoning", "The scripted endpoint observed the profile effort and the higher-priority command-line override.",
    ),
    "agent-metadata-discovery": (
        "agents", "The bundled executable listed the generated project agent with its authored model selector.",
    ),
    "project-http-pending-approval": (
        "mcp", "The bundled executable read the generated project HTTP entry and reported it pending approval.",
    ),
    "project-public-docs-tool-discovery": (
        "mcp", "After local project approval, the bundled executable connected to OpenAI Docs and listed its tools.",
    ),
    "claude-child-model-request": (
        "models", "The generated subagent selected its own model in a request to a scripted loopback Messages endpoint.",
    ),
    "claude-child-effort-request": (
        "reasoning", "The generated subagent selected its own output_config.effort in the loopback request.",
    ),
    "claude-child-mcp-tool-filter": (
        "mcp", "The generated subagent tool list excluded the local fixture visible in the parent session.",
    ),
    "qwen-daemon-agent-discovery": ("agents", "The daemon listed the generated project agent."),
    "qwen-daemon-mcp-pending-approval": (
        "mcp", "The daemon read the generated project server and kept it disconnected pending approval.",
    ),
    "qwen-daemon-approved-stdio-discovery": (
        "mcp", "The daemon connected and requested the fixture tool list after approval of the exact configuration.",
    ),
    "qwen-daemon-startup-model": ("models", "The daemon confirmed the generated startup request's exact model route."),
    "qwen-daemon-session-effort": ("reasoning", "Live session context reflected the generated model's effort setting."),
    "opencode-v2-disabled-mcp-load": ("mcp", "The V2 server read the generated MCP entry with discovery disabled."),
    "opencode-v2-model-variant-catalog": ("models", "The V2 server listed built-in model and variant IDs."),
    "opencode-v2-agent-variant-load": (
        "reasoning", "The V2 server reported the generated subagent's exact provider, model, and named variant.",
    ),
    "opencode-v2-stdio-discovery": (
        "mcp", "The V2 server initialized the local fixture and requested its tool list after explicit enablement.",
    ),
    "opencode-v2-variant-effort-request": (
        "reasoning", "The CLI applied a named variant's reasoning effort in a request to a scripted loopback endpoint.",
    ),
    "opencode-v2-child-skill-use": (
        "skills", "The delegated subagent loaded its allowed Skill and received the Skill instructions.",
    ),
    "opencode-v2-child-mcp-filter": (
        "mcp", "The generated subagent's tool list excluded the stdio fixture visible to the parent session.",
    ),
    "kilo-stdio-discovery": (
        "mcp", "Kilo initialized the selected project stdio fixture and requested its tools.",
    ),
    "kilo-model-variant-catalog": (
        "models", "Kilo listed built-in model IDs and a local fixture model and variant. Weaver imported their selectors.",
    ),
    "kilo-agent-variant-load": (
        "reasoning", "Kilo reported the generated subagent's configured model and named variant from a synthetic catalog.",
    ),
    "kilo-cli-variant-request": (
        "reasoning", "A named variant sent high reasoning effort to the local endpoint. Omitting the variant omitted the field.",
    ),
    "kilo-child-ambient-mcp": (
        "mcp", "The generated subagent could see the fixture tool explicitly allowed by the parent project.",
    ),
    "kilo-extension-subagent-discovery": (
        "agents", "The extension sent the generated subagent and its model to the sidebar's complete agent list.",
    ),
    "kilo-extension-model-selection": (
        "models", "The extension's sidebar model picker selected the synthetic local provider model.",
    ),
    "kilo-extension-variant-selection": (
        "reasoning", "The sidebar exposed the local model's named variant and displayed it after selection.",
    ),
    "cline-agent-file-inventory": (
        "agents", "Cline listed the generated project agent file in its configuration inventory.",
    ),
    "cline-disabled-mcp-registration": (
        "mcp", "Cline listed the manually staged stdio entry as disabled and made no fixture requests.",
    ),
    "cline-session-model-request": (
        "models", "The generated CLI arguments selected the OpenRouter model ID sent to a scripted loopback endpoint.",
    ),
    "cline-session-effort-request": (
        "reasoning", "The generated CLI arguments sent the selected thinking level to the scripted endpoint.",
    ),
    "cline-child-skill-use": (
        "skills", "The configured subagent called its allowed Skill and received the generated Skill instructions.",
    ),
    "cline-child-mcp-filter": (
        "mcp", "The configured subagent received the selected built-in tools. The parent alone could see its MCP fixture.",
    ),
    "cline-desktop-model-catalog": (
        "models", "The Desktop backend listed its bundled OpenRouter model IDs and reasoning capability flags.",
    ),
    "cline-desktop-session-model-request": (
        "models", "The Desktop session sent the selected OpenRouter model ID to a scripted loopback endpoint.",
    ),
    "cline-desktop-session-effort-request": (
        "reasoning", "The Desktop session applied the selected thinking effort in its loopback model request.",
    ),
    "cline-desktop-max-effort-fallback": (
        "reasoning", "Desktop 0.0.49 sent medium effort when its chat backend received max for the tested model.",
    ),
    "factory-droid-discovery": (
        "agents", "Droid advertised the generated custom droid. The unsigned session stopped at daemon authentication.",
    ),
    "factory-cli-skill-use": (
        "skills", "Droid called the project Skill and received the generated Skill instructions.",
    ),
    "factory-cli-model-catalog": (
        "models", "The signed-out JSON-RPC catalog listed model IDs and their advertised reasoning efforts.",
    ),
    "factory-cli-custom-model-request": (
        "models", "The CLI accepted the catalog's custom selector and sent its model ID to a scripted local endpoint.",
    ),
    "factory-cli-custom-effort-request": (
        "reasoning", "Custom-model exec arguments sent low, medium, and high, and off omitted the effort field.",
    ),
    "kimi-web-stdio-tool-discovery": (
        "mcp", "Kimi's workspace registry loaded the project MCP entry after trust and initiated stdio tool discovery.",
    ),
    "kimi-web-model-catalog": (
        "models", "The local Web API enumerated three configured fixture aliases with their advertised effort lists.",
    ),
    "kimi-web-session-model-effort": (
        "reasoning", "The staged session request bound the named model and high effort in a loopback request.",
    ),
    "kimi-web-subagent-pool-effort": (
        "reasoning", "A spawned custom subagent used an alternate pool model and the shared high effort.",
    ),
    "qoder-cli-stdio-healthcheck": (
        "mcp", "The signed-out CLI connected, initialized, and pinged the generated project stdio fixture.",
    ),
    "qoder-cli-context7-https-healthcheck": (
        "mcp", "The signed-out CLI connected to the generated Context7 entry over HTTPS using the host TLS root.",
    ),
    "project-stdio-healthcheck": (
        "mcp", "The bundled executable connected to the generated project stdio fixture after isolated user approval.",
    ),
    "signed-out-model-list": (
        "models", "The bundled executable listed signed-out model aliases and their resolved IDs.",
    ),
    "signed-out-effort-options": (
        "reasoning", "The bundled executable advertised effort levels for the signed-out model aliases.",
    ),
}
_OBSERVATION_FIELDS = {
    "product_id", "surface_id", "product_version", "engine_version", "os_id", "os_variant",
    "architecture", "interface", "environment", "checked_on", "source_commit", "checks",
}
_OBSERVATION_INTERFACES = {
    ("codex", "cli", "app-server-stdio"),
    ("codex", "cli", "exec-loopback"),
    ("codex", "desktop", "app-server-stdio"),
    ("codex", "ide", "app-server-stdio"),
    ("copilot", "vscode", "bundled-runtime"),
    ("claude-code", "vscode", "bundled-runtime"),
    ("qwen-code", "cli", "daemon-http"),
    ("qwen-code", "cli", "exec-loopback"),
    ("opencode", "cli", "serve-http"),
    ("opencode", "cli", "exec-loopback"),
    ("kilo", "cli", "serve-http"),
    ("kilo", "cli", "exec-loopback"),
    ("kilo", "ide", "vscode-extension-host"),
    ("cline", "cli", "config-inventory"),
    ("cline", "cli", "exec-loopback"),
    ("cline", "desktop", "desktop-sidecar"),
    ("factory", "cli", "exec-loopback"),
    ("factory", "cli", "exec-jsonrpc"),
    ("kimi-code", "cli", "local-web-api"),
    ("qoder", "cli", "config-inventory"),
}


@dataclass(frozen=True)
class ScopeTarget:
    product_id: str
    surface_id: str


@dataclass(frozen=True)
class RequestedItem:
    id: str
    label: str
    aliases: tuple[str, ...]
    targets: tuple[ScopeTarget, ...]


def _alias_key(value: object) -> str:
    text = _string(value, "Requested product alias")
    if text != text.strip():
        raise WeaverError("Requested product alias must not contain surrounding whitespace")
    return text.casefold()


def _load_catalog(root: Path) -> dict[str, Any]:
    data = _strict_object(
        load_json(Path(root) / "catalogs/requested-coverage.json"),
        "requested coverage catalog", {"schema_version", "requested_items"},
        {"runtime_observations"},
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise WeaverError("Requested coverage catalog: unsupported schema version")
    return data


def _load_scope(data: dict[str, Any], integrations: dict[str, IntegrationSpec]) -> tuple[RequestedItem, ...]:
    entries = data["requested_items"]
    if not isinstance(entries, list) or not entries:
        raise WeaverError("Requested coverage catalog: non-empty requested_items list required")
    items: list[RequestedItem] = []
    identifiers: set[str] = set()
    aliases: set[str] = set()
    for value in entries:
        entry = _strict_object(value, "requested item", {"id", "label", "aliases", "targets"})
        identifier = _canonical_id(entry["id"], "requested item id")
        if identifier in identifiers:
            raise WeaverError("Requested coverage catalog: duplicate requested item id")
        identifiers.add(identifier)
        label = _string(entry["label"], "requested item label")
        item_aliases = _string_list(entry["aliases"], "requested item aliases")
        if not item_aliases:
            raise WeaverError("Requested coverage catalog: explicit aliases required")
        target_data = entry["targets"]
        if not isinstance(target_data, list) or not target_data:
            raise WeaverError("Requested coverage catalog: explicit targets required")
        targets: list[ScopeTarget] = []
        for target_value in target_data:
            target = _strict_object(target_value, "requested target", {"product_id", "surface_id"})
            product = _canonical_id(target["product_id"], "requested product id")
            surface = _canonical_id(target["surface_id"], "requested surface id")
            if product not in integrations or surface not in integrations[product].surface_ids():
                raise WeaverError("Requested coverage catalog: target is absent from integration registry")
            parsed = ScopeTarget(product, surface)
            if parsed in targets:
                raise WeaverError("Requested coverage catalog: duplicate target")
            targets.append(parsed)
        products = {target.product_id for target in targets}
        for alias in item_aliases:
            key = _alias_key(alias)
            if key in aliases:
                raise WeaverError("Requested coverage catalog: alias collision")
            if key in integrations and products != {key}:
                raise WeaverError("Requested coverage catalog: alias shadows a canonical product id")
            aliases.add(key)
        items.append(RequestedItem(identifier, label, item_aliases, tuple(targets)))
    return tuple(items)


def load_requested_scope(root: Path) -> tuple[RequestedItem, ...]:
    """Load explicit mappings and reject missing references or ambiguous catalog aliases."""
    return _load_scope(_load_catalog(root), load_integrations(root))


def resolve_product_alias(root: Path, value: str) -> str:
    """Resolve one canonical product, refusing aliases for a group of products."""
    key = _alias_key(value)
    integrations = load_integrations(root)
    scope = _load_scope(_load_catalog(root), integrations)
    if key in integrations:
        return key
    for item in scope:
        if key in {_alias_key(alias) for alias in item.aliases}:
            products = {target.product_id for target in item.targets}
            if len(products) != 1:
                raise WeaverError("Requested alias names multiple products; inspect coverage targets")
            return next(iter(products))
    raise WeaverError("Unknown product alias; inspect inventory or coverage")


def _observation_field(value: object, pattern: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value, flags=re.ASCII):
        raise WeaverError(f"Runtime observation: invalid {label}")
    return value


def _load_observations(
    data: dict[str, Any], scope: tuple[RequestedItem, ...],
) -> dict[ScopeTarget, list[dict[str, Any]]]:
    entries = data.get("runtime_observations", [])
    if not isinstance(entries, list):
        raise WeaverError("Runtime observations must be a list")
    targets = {target for item in scope for target in item.targets}
    observations: dict[ScopeTarget, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, ...]] = set()
    for value in entries:
        row = _strict_object(value, "runtime observation", _OBSERVATION_FIELDS, {"extension_version"})
        product = _canonical_id(row["product_id"], "runtime observation product")
        surface = _canonical_id(row["surface_id"], "runtime observation surface")
        target = ScopeTarget(product, surface)
        if target not in targets:
            raise WeaverError("Runtime observation must reference a requested registry surface")
        checked_on = _observation_field(row["checked_on"], r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "date")
        try:
            date.fromisoformat(checked_on)
        except ValueError:
            raise WeaverError("Runtime observation: invalid date") from None
        os_id = _canonical_id(row["os_id"], "runtime observation OS")
        if os_id not in OS_IDS:
            raise WeaverError("Runtime observation: unknown OS")
        identity = {
            "product_version": _observation_field(row["product_version"], r"[0-9][A-Za-z0-9.+_-]{0,63}", "product version"),
            "engine_version": _observation_field(row["engine_version"], r"[0-9][A-Za-z0-9.+_-]{0,63}", "engine version"),
            "os_id": os_id,
            "os_variant": _observation_field(row["os_variant"], rf"{os_id}:[A-Za-z0-9][A-Za-z0-9.() _:-]{{0,95}}", "OS variant"),
            "architecture": _observation_field(row["architecture"], r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", "architecture"),
            "interface": _canonical_id(row["interface"], "runtime observation interface"),
            "environment": _canonical_id(row["environment"], "runtime observation environment"),
            "checked_on": checked_on,
            "source_commit": _observation_field(row["source_commit"], r"[0-9a-f]{40}", "source commit"),
        }
        environments = {
            ("qwen-code", "cli"): {"isolated-release-loopback"},
            ("opencode", "cli"): {"isolated-release-loopback"},
            ("kilo", "cli"): {"isolated-release-loopback"},
            ("kilo", "ide"): {"isolated-release-loopback"},
            ("cline", "cli"): {"isolated-release-loopback"},
            ("cline", "desktop"): {"isolated-release-loopback"},
            ("factory", "cli"): {"isolated-release-loopback"},
            ("kimi-code", "cli"): {"isolated-release-loopback"},
            ("claude-code", "vscode"): {"isolated-signed-out", "isolated-loopback"},
        }.get((product, surface), {"isolated-signed-out"})
        if ((product, surface, identity["interface"]) not in _OBSERVATION_INTERFACES
                or identity["environment"] not in environments):
            raise WeaverError("Runtime observation: unsupported interface or environment")
        if "extension_version" in row:
            identity["extension_version"] = _observation_field(
                row["extension_version"], r"[0-9][A-Za-z0-9.+_-]{0,63}", "extension version")
        is_extension = (identity["interface"] in {"bundled-runtime", "vscode-extension-host"}
                        or (product, surface) == ("codex", "ide"))
        if is_extension != ("extension_version" in identity):
            raise WeaverError("Runtime observation: extension version must match the extension runtime scope")
        checks = row["checks"]
        if not isinstance(checks, list) or not checks:
            raise WeaverError("Runtime observation requires explicit checks")
        parsed_checks = []
        for entry in checks:
            check = _strict_object(entry, "runtime observation check", {"check", "evidence_sha256"})
            name = _canonical_id(check["check"], "runtime observation check")
            if name not in _OBSERVATION_CHECKS:
                raise WeaverError("Runtime observation: unknown check")
            digest = _observation_field(check["evidence_sha256"], r"[0-9a-f]{64}", "evidence hash")
            key = (product, surface, *identity.values(), name)
            if key in seen:
                raise WeaverError("Duplicate runtime observation check")
            seen.add(key)
            capability, outcome = _OBSERVATION_CHECKS[name]
            parsed_checks.append({"check": name, "capability": capability, "outcome": outcome, "evidence_sha256": digest})
        observations[target].append({**identity, "checks": parsed_checks})
    return observations


def coverage_report(root: Path) -> dict[str, Any]:
    """Describe mapping and evidence gaps without treating catalog presence as support."""
    integrations = load_integrations(root)
    data = _load_catalog(root)
    scope = _load_scope(data, integrations)
    observations = _load_observations(data, scope)
    rows: list[dict[str, Any]] = []
    referenced: set[ScopeTarget] = set()
    states: dict[str, int] = {}
    pending_runtime = 0
    missing_documentation = 0
    for item in scope:
        targets: list[dict[str, Any]] = []
        for target in item.targets:
            product = integrations[target.product_id]
            surface = product.surface(target.surface_id)
            capabilities: dict[str, Any] = {}
            for name in CAPABILITY_NAMES:
                evidence = surface.capabilities[name]
                capabilities[name] = {
                    "state": evidence.state,
                    "sources": list(evidence.sources),
                    "checked_on": evidence.checked_on,
                    "version_constraint": evidence.version_constraint,
                    "documentation_status": "recorded" if evidence.sources else "missing",
                    "fixture_tests": list(evidence.fixture_tests),
                    "fixture_status": "recorded" if evidence.fixture_tests else "pending",
                    "runtime_checks": list(evidence.runtime_checks),
                    "runtime_status": "recorded" if evidence.runtime_checks else "pending",
                }
                if target not in referenced:
                    states[evidence.state] = states.get(evidence.state, 0) + 1
                    pending_runtime += not bool(evidence.runtime_checks)
                    missing_documentation += not bool(evidence.sources)
            targets.append({
                "product_id": target.product_id, "product_name": product.name,
                "kind": product.kind, "surface_id": target.surface_id,
                "config_scope": surface.config_scope, "capabilities": capabilities,
                "runtime_observations": observations.get(target, []),
            })
            referenced.add(target)
        rows.append({
            "id": item.id, "label": item.label, "aliases": list(item.aliases),
            "disposition": "mapped", "targets": targets,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "requested_items": rows,
        "summary": {
            "requested_items": len(rows), "mapped_items": len(rows),
            "referenced_surfaces": len(referenced), "capability_states": states,
            "missing_documentation_capabilities": missing_documentation,
            "pending_runtime_capabilities": pending_runtime,
            "surfaces_with_runtime_observations": len(observations),
            "runtime_observation_checks": sum(len(record["checks"]) for records in observations.values()
                                              for record in records),
        },
        "limitations": list(LIMITATIONS),
    }
