"""Offline requested-scope mapping, separate from capability and runtime support."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import WeaverError, load_json
from .integrations import (
    CAPABILITY_NAMES,
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
    "No installed-version, device, entitlement or exhaustive model-catalog verification is performed.",
)


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


def _load_scope(root: Path, integrations: dict[str, IntegrationSpec]) -> tuple[RequestedItem, ...]:
    data = _strict_object(
        load_json(Path(root) / "catalogs/requested-coverage.json"),
        "requested coverage catalog", {"schema_version", "requested_items"},
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise WeaverError("Requested coverage catalog: unsupported schema version")
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
    return _load_scope(root, load_integrations(root))


def resolve_product_alias(root: Path, value: str) -> str:
    """Resolve one canonical product, refusing aliases for a group of products."""
    key = _alias_key(value)
    integrations = load_integrations(root)
    scope = _load_scope(root, integrations)
    if key in integrations:
        return key
    for item in scope:
        if key in {_alias_key(alias) for alias in item.aliases}:
            products = {target.product_id for target in item.targets}
            if len(products) != 1:
                raise WeaverError("Requested alias names multiple products; inspect coverage targets")
            return next(iter(products))
    raise WeaverError("Unknown product alias; inspect inventory or coverage")


def coverage_report(root: Path) -> dict[str, Any]:
    """Describe mapping and evidence gaps without treating catalog presence as support."""
    integrations = load_integrations(root)
    scope = _load_scope(root, integrations)
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
        },
        "limitations": list(LIMITATIONS),
    }
