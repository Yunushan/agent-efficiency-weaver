import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import AbstractSet, Any, NoReturn, cast
from urllib.parse import urlparse

from .config import WeaverError, load_json, safe_https_url

SCHEMA_VERSION = 1
CAPABILITY_NAMES = ("mcp", "instructions", "skills", "provider", "os")
CAPABILITY_STATES = {"available", "manual", "unsupported", "unknown"}
CONFIG_SCOPES = {"project", "user", "app", "request", "none"}
OS_IDS = ("windows", "macos", "linux", "bsd", "solaris", "android", "ios")
OS_CAPABILITY_NAMES = ("core_runtime", "native_agent", "manual_remote")
EXPECTED_IDS = (
    "abacus",
    "aider",
    "amp",
    "antigravity",
    "claude-code",
    "cline",
    "codex",
    "copilot",
    "cursor",
    "devin",
    "goose",
    "junie",
    "kilo",
    "kiro",
    "opencode",
    "openhands",
    "openrouter",
    "pi",
    "qoder",
    "zed",
)
# Historical baseline only. The catalog is authoritative and may add products.
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
INTEGRATION_KEYS = {"id", "name", "kind", "default_surface", "surfaces"}
SURFACE_REQUIRED_KEYS = {
    "id",
    "mcp_dialect",
    "config_fields",
    "config_scope",
    "native_path_hint",
    "instruction_mode",
    "instruction_paths",
    "skills_path",
    "os_workflows",
    "capabilities",
}
SURFACE_OPTIONAL_KEYS = {"documented_location", "provider"}
EVIDENCE_KEYS = {
    "state",
    "sources",
    "checked_on",
    "version_constraint",
    "fixture_tests",
    "runtime_checks",
}
OS_KEYS = {"id", "name", "family", "variants", "capabilities"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class CapabilityEvidence:
    state: str
    sources: tuple[str, ...]
    checked_on: str | None
    version_constraint: str | None
    fixture_tests: tuple[str, ...]
    runtime_checks: tuple[str, ...]


@dataclass(frozen=True)
class SurfaceSpec:
    id: str
    mcp_dialect: str | None
    config_fields: tuple[str, ...]
    config_scope: str
    native_path_hint: str | None
    documented_location: str | None
    instruction_mode: str
    instruction_paths: tuple[str, ...]
    skills_path: str | None
    os_workflows: dict[str, str]
    capabilities: dict[str, CapabilityEvidence]
    provider: dict[str, Any] | None


@dataclass(frozen=True)
class IntegrationSpec:
    id: str
    name: str
    kind: str
    default_surface: str
    surfaces: tuple[SurfaceSpec, ...]

    def surface_ids(self) -> tuple[str, ...]:
        return tuple(surface.id for surface in self.surfaces)

    def surface(self, surface_id: str) -> SurfaceSpec:
        for surface in self.surfaces:
            if surface.id == surface_id:
                return surface
        raise WeaverError(
            f"Unsupported surface {surface_id!r} for integration {self.id!r}; "
            f"available surfaces: {', '.join(self.surface_ids())}"
        )


@dataclass(frozen=True)
class OperatingSystemSpec:
    id: str
    name: str
    family: str
    variants: tuple[str, ...]
    capabilities: dict[str, CapabilityEvidence]
    core_runtime: str


def _fail(message: str) -> NoReturn:
    raise WeaverError(message)


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        return load_json(path)
    except WeaverError as exc:
        raise WeaverError(f"Invalid {label} catalog: {exc}") from None


def _strict_object(value: object, label: str, required: AbstractSet[str], optional: AbstractSet[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label}: expected object")
    missing = set(required) - set(value)
    unknown = set(value) - set(required) - set(optional)
    if missing:
        _fail(f"{label}: missing fields {', '.join(sorted(missing))}")
    if unknown:
        _fail(f"{label}: unknown fields are not accepted")
    return cast(dict[str, Any], value)


def _string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        _fail(f"{label}: non-empty string required")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail(f"{label}: invalid Unicode")
    for char in value:
        code = ord(char)
        if unicodedata.category(char) in {"Cc", "Cs"}:
            _fail(f"{label}: control characters are not allowed")
        if 0xFDD0 <= code <= 0xFDEF or (code & 0xFFFE) == 0xFFFE:
            _fail(f"{label}: invalid Unicode")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail(f"{label}: expected list")
    items: list[str] = []
    for index, item in enumerate(value):
        items.append(_string(item, f"{label}[{index}]"))
    return tuple(items)


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_INVALID_CHARS = set('<>"|?*')


def _safe_relative(value: object, label: str) -> str | None:
    if value is None:
        return None
    path = _string(value, label)
    if (
        path.startswith("/")
        or path.startswith("~")
        or "\\" in path
        or ":" in path
        or "//" in path
        or path.endswith("/")
    ):
        _fail(f"{label}: must be a relative POSIX path")
    for part in path.split("/"):
        if part in {"", ".", ".."}:
            _fail(f"{label}: path traversal or absolute path is not allowed")
        if any(char in _WINDOWS_INVALID_CHARS for char in part):
            _fail(f"{label}: Windows-invalid path characters are not allowed")
        if part.endswith(" ") or part.endswith("."):
            _fail(f"{label}: Windows-invalid trailing space or dot")
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            _fail(f"{label}: Windows-reserved path component")
    return path


def _native_hint(value: object, label: str, config_scope: str) -> str | None:
    if config_scope in {"project", "request"}:
        return _safe_relative(value, label)
    return _optional_string(value, label)


def _canonical_id(value: object, label: str) -> str:
    identifier = _string(value, label)
    if not ID_PATTERN.fullmatch(identifier):
        _fail(f"{label}: lowercase portable canonical id required")
    _safe_relative(identifier, label)
    return identifier


def _reference(value: object, label: str) -> str:
    path = _string(value, label)
    if "\\" in path or ":" in path or path.startswith("~") or path.startswith("/"):
        _fail(f"{label}: must be a relative reference")
    if ".." in PurePosixPath(path).parts:
        _fail(f"{label}: path traversal is not allowed")
    return path


def _evidence(value: object, label: str) -> CapabilityEvidence:
    data = _strict_object(value, label, EVIDENCE_KEYS)
    state = _string(data["state"], f"{label}.state")
    if state not in CAPABILITY_STATES:
        _fail(f"{label}.state: expected one of {', '.join(sorted(CAPABILITY_STATES))}")
    sources = _string_list(data["sources"], f"{label}.sources")
    for index, source in enumerate(sources):
        if not safe_https_url(source, allow_fragment=True):
            _fail(f"{label}.sources[{index}]: credential-free HTTPS source without query or token fragment required")
    checked_on = data["checked_on"]
    if checked_on is not None:
        checked_on = _string(checked_on, f"{label}.checked_on")
        if not DATE_PATTERN.match(checked_on):
            _fail(f"{label}.checked_on: YYYY-MM-DD required")
    if state in {"available", "manual", "unsupported"}:
        if not sources:
            _fail(f"{label}: documented evidence required for state {state}")
        if checked_on is None:
            _fail(f"{label}: checked_on required for state {state}")
    version_constraint = _optional_string(data["version_constraint"], f"{label}.version_constraint")
    fixture_tests = _string_list(data["fixture_tests"], f"{label}.fixture_tests")
    runtime_checks = _string_list(data["runtime_checks"], f"{label}.runtime_checks")
    return CapabilityEvidence(
        state=state,
        sources=sources,
        checked_on=checked_on,
        version_constraint=version_constraint,
        fixture_tests=fixture_tests,
        runtime_checks=runtime_checks,
    )


def _os_workflows(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        _fail(f"{label}: expected object")
    if set(value) != set(OS_IDS):
        _fail(f"{label}: must define exactly {', '.join(OS_IDS)}")
    result = {}
    for os_id in OS_IDS:
        state = _string(value[os_id], f"{label}.{os_id}")
        if state not in CAPABILITY_STATES:
            _fail(f"{label}.{os_id}: invalid capability state")
        result[os_id] = state
    return result


def _provider(value: object, label: str, integration_id: str) -> dict[str, Any] | None:
    if value is None:
        return None
    data = _strict_object(
        value,
        label,
        {"base_url", "endpoint", "model_selection", "model_ids", "secret_env", "mcp_service_enabled"},
    )
    base_url = _string(data["base_url"], f"{label}.base_url")
    valid_url = safe_https_url(base_url)
    if integration_id == "lm-studio" and base_url.startswith("http://"):
        try:
            parsed = urlparse(base_url)
            valid_url = (parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                         and safe_https_url("https://" + base_url[len("http://"):]))
        except ValueError:
            valid_url = False
    if not valid_url:
        _fail(f"{label}.base_url: credential-free HTTPS URL or explicit local LM Studio URL required")
    endpoint = _string(data["endpoint"], f"{label}.endpoint")
    if (not endpoint.startswith("/") or endpoint.startswith("//")
            or not safe_https_url("https://endpoint.invalid" + endpoint)
            or any(part in {".", ".."} for part in endpoint.split("/"))):
        _fail(f"{label}.endpoint: absolute endpoint path without credentials, query or fragment required")
    model_selection = _string(data["model_selection"], f"{label}.model_selection")
    if model_selection != "explicit":
        _fail(f"{label}.model_selection: explicit selection required")
    model_ids = list(_string_list(data["model_ids"], f"{label}.model_ids"))
    secret_env = _optional_string(data["secret_env"], f"{label}.secret_env")
    if secret_env is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]*", secret_env):
        _fail(f"{label}.secret_env: environment variable name required")
    if data["mcp_service_enabled"] is not False:
        _fail(f"{label}.mcp_service_enabled: generated provider MCP services must remain disabled")
    return {
        "base_url": base_url,
        "endpoint": endpoint,
        "model_selection": model_selection,
        "model_ids": model_ids,
        "secret_env": secret_env,
        "mcp_service_enabled": data["mcp_service_enabled"],
    }


def _surface(value: object, integration_id: str) -> SurfaceSpec:
    label = f"integration {integration_id} surface"
    data = _strict_object(value, label, SURFACE_REQUIRED_KEYS, SURFACE_OPTIONAL_KEYS)
    surface_id = _canonical_id(data["id"], f"{label}.id")
    label = f"integration {integration_id} surface {surface_id}"
    dialect = _optional_string(data["mcp_dialect"], f"{label}.mcp_dialect")
    config_fields = _string_list(data["config_fields"], f"{label}.config_fields")
    config_scope = _string(data["config_scope"], f"{label}.config_scope")
    if config_scope not in CONFIG_SCOPES:
        _fail(f"{label}.config_scope: expected one of {', '.join(sorted(CONFIG_SCOPES))}")
    native_path = _native_hint(data["native_path_hint"], f"{label}.native_path_hint", config_scope)
    documented_location = _optional_string(
        data.get("documented_location"), f"{label}.documented_location"
    )
    instruction_mode = _string(data["instruction_mode"], f"{label}.instruction_mode")
    instruction_paths = _string_list(data["instruction_paths"], f"{label}.instruction_paths")
    for index, path in enumerate(instruction_paths):
        _reference(path, f"{label}.instruction_paths[{index}]")
    skills_path = _safe_relative(data["skills_path"], f"{label}.skills_path")
    os_workflows = _os_workflows(data["os_workflows"], f"{label}.os_workflows")
    capabilities_data = _strict_object(
        data["capabilities"],
        f"{label}.capabilities",
        set(CAPABILITY_NAMES),
    )
    capabilities = {
        name: _evidence(capabilities_data[name], f"{label}.capabilities.{name}")
        for name in CAPABILITY_NAMES
    }
    provider = _provider(data.get("provider"), f"{label}.provider", integration_id)
    return SurfaceSpec(
        id=surface_id,
        mcp_dialect=dialect,
        config_fields=config_fields,
        config_scope=config_scope,
        native_path_hint=native_path,
        documented_location=documented_location,
        instruction_mode=instruction_mode,
        instruction_paths=instruction_paths,
        skills_path=skills_path,
        os_workflows=os_workflows,
        capabilities=capabilities,
        provider=provider,
    )


def _integration(value: object, index: int) -> IntegrationSpec:
    label = f"integrations[{index}]"
    data = _strict_object(value, label, INTEGRATION_KEYS)
    integration_id = _canonical_id(data["id"], f"{label}.id")
    label = f"integration {integration_id}"
    name = _string(data["name"], f"{label}.name")
    kind = _string(data["kind"], f"{label}.kind")
    if kind not in {"agent", "provider", "hybrid"}:
        _fail(f"{label}.kind: expected agent, provider or hybrid")
    default_surface = _canonical_id(data["default_surface"], f"{label}.default_surface")
    surfaces_data = data["surfaces"]
    if not isinstance(surfaces_data, list) or not surfaces_data:
        _fail(f"{label}.surfaces: non-empty list required")
    parsed_surfaces = [_surface(item, integration_id) for item in surfaces_data]
    surface_ids = [surface.id for surface in parsed_surfaces]
    if len(surface_ids) != len(set(surface_ids)):
        _fail(f"{label}.surfaces: duplicate surface id")
    surfaces = tuple(parsed_surfaces)
    if default_surface not in surface_ids:
        _fail(f"{label}.default_surface: no matching surface")
    if kind == "provider":
        if any(surface.provider is None for surface in surfaces):
            _fail(f"{label}: provider surfaces require provider metadata")
    elif kind == "hybrid":
        metadata_count = sum(surface.provider is not None for surface in surfaces)
        if metadata_count == 0 or metadata_count == len(surfaces):
            _fail(f"{label}: hybrid requires both provider and host surfaces")
    elif any(surface.provider is not None for surface in surfaces):
        _fail(f"{label}: agent surfaces cannot define provider metadata")
    return IntegrationSpec(
        id=integration_id,
        name=name,
        kind=kind,
        default_surface=default_surface,
        surfaces=surfaces,
    )


def load_integrations(root: Path) -> dict[str, IntegrationSpec]:
    path = Path(root) / "catalogs/integrations.json"
    data = _strict_object(
        _read_object(path, "integrations"),
        "integrations catalog",
        {"schema_version", "integrations"},
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        _fail(f"integrations catalog: schema_version {SCHEMA_VERSION} required")
    entries = data["integrations"]
    if not isinstance(entries, list) or not entries:
        _fail("integrations catalog: integrations must be a non-empty list")
    integrations = {}
    for index, entry in enumerate(entries):
        integration = _integration(entry, index)
        if integration.id in integrations:
            _fail(f"integrations catalog: duplicate integration id {integration.id!r}")
        integrations[integration.id] = integration
    if len({integration.name.casefold() for integration in integrations.values()}) != len(integrations):
        _fail("integrations catalog: integration names must be unique")
    return {key: integrations[key] for key in sorted(integrations)}


def get_integration(integration_id: str, root: Path) -> IntegrationSpec:
    if not isinstance(integration_id, str) or not integration_id:
        _fail("Integration id required")
    integrations = load_integrations(root)
    try:
        return integrations[integration_id]
    except KeyError:
        raise WeaverError(f"Unknown integration: {integration_id}") from None


def get_surface(integration_id: str, surface_id: str, root: Path) -> SurfaceSpec:
    integration = get_integration(integration_id, root)
    return integration.surface(surface_id)


def _os_operating_system(value: object, index: int) -> OperatingSystemSpec:
    label = f"operating_systems[{index}]"
    data = _strict_object(value, label, OS_KEYS)
    os_id = _string(data["id"], f"{label}.id")
    label = f"operating system {os_id}"
    name = _string(data["name"], f"{label}.name")
    family = _string(data["family"], f"{label}.family")
    variants = _string_list(data["variants"], f"{label}.variants")
    capabilities_data = _strict_object(
        data["capabilities"],
        f"{label}.capabilities",
        set(OS_CAPABILITY_NAMES),
    )
    capabilities = {
        capability: _evidence(capabilities_data[capability], f"{label}.capabilities.{capability}")
        for capability in OS_CAPABILITY_NAMES
    }
    return OperatingSystemSpec(
        id=os_id,
        name=name,
        family=family,
        variants=variants,
        capabilities=capabilities,
        core_runtime=capabilities["core_runtime"].state,
    )


def load_operating_systems(root: Path) -> dict[str, OperatingSystemSpec]:
    path = Path(root) / "catalogs/operating-systems.json"
    data = _strict_object(
        _read_object(path, "operating systems"),
        "operating systems catalog",
        {"schema_version", "operating_systems"},
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        _fail(f"operating systems catalog: schema_version {SCHEMA_VERSION} required")
    entries = data["operating_systems"]
    if not isinstance(entries, list):
        _fail("operating systems catalog: operating_systems must be a list")
    systems = {}
    for index, entry in enumerate(entries):
        system = _os_operating_system(entry, index)
        if system.id in systems:
            _fail(f"operating systems catalog: duplicate id {system.id!r}")
        if system.family != system.id:
            _fail(f"operating system {system.id}: family must match id")
        systems[system.id] = system
    if set(systems) != set(OS_IDS):
        missing = sorted(set(OS_IDS) - set(systems))
        extra = sorted(set(systems) - set(OS_IDS))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        _fail("operating systems catalog: " + "; ".join(detail))
    return {key: systems[key] for key in sorted(systems)}


def _evidence_data(evidence: CapabilityEvidence) -> dict[str, Any]:
    return {
        "state": evidence.state,
        "sources": list(evidence.sources),
        "checked_on": evidence.checked_on,
        "version_constraint": evidence.version_constraint,
        "fixture_tests": list(evidence.fixture_tests),
        "runtime_checks": list(evidence.runtime_checks),
    }


def _surface_data(surface: SurfaceSpec) -> dict[str, Any]:
    data = {
        "id": surface.id,
        "mcp_dialect": surface.mcp_dialect,
        "config_fields": list(surface.config_fields),
        "config_scope": surface.config_scope,
        "native_path_hint": surface.native_path_hint,
        "documented_location": surface.documented_location,
        "instruction_mode": surface.instruction_mode,
        "instruction_paths": list(surface.instruction_paths),
        "skills_path": surface.skills_path,
        "os_workflows": dict(surface.os_workflows),
        "capabilities": {
            name: _evidence_data(surface.capabilities[name]) for name in CAPABILITY_NAMES
        },
    }
    if surface.provider is not None:
        data["provider"] = dict(surface.provider)
    return data


def _integration_data(integration: IntegrationSpec) -> dict[str, Any]:
    return {
        "id": integration.id,
        "name": integration.name,
        "kind": integration.kind,
        "default_surface": integration.default_surface,
        "surfaces": [_surface_data(surface) for surface in integration.surfaces],
    }


def _os_data(system: OperatingSystemSpec) -> dict[str, Any]:
    return {
        "id": system.id,
        "name": system.name,
        "family": system.family,
        "variants": list(system.variants),
        "capabilities": {
            name: _evidence_data(system.capabilities[name]) for name in OS_CAPABILITY_NAMES
        },
    }


def integration_inventory(root: Path) -> dict[str, Any]:
    integrations = load_integrations(root)
    systems = load_operating_systems(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "integrations": [_integration_data(integrations[key]) for key in integrations],
        "operating_systems": [_os_data(systems[key]) for key in systems],
    }


def inventory_text(root: Path) -> str:
    data = integration_inventory(root)
    integrations = data["integrations"]
    agents = sum(1 for item in integrations if item["kind"] == "agent")
    providers = sum(1 for item in integrations if item["kind"] == "provider")
    hybrids = sum(1 for item in integrations if item["kind"] == "hybrid")
    lines = [
        f"schema_version: {data['schema_version']}",
        f"agents: {agents}",
        f"providers: {providers}",
        f"hybrids: {hybrids}",
        "integrations:",
    ]
    for item in integrations:
        surface_ids = ",".join(surface["id"] for surface in item["surfaces"])
        lines.append(f"{item['id']}\t{item['kind']}\t{item['default_surface']}\t{surface_ids}")
    os_ids = ", ".join(item["id"] for item in data["operating_systems"])
    lines.append(f"operating_systems: {os_ids}")
    return "\n".join(lines) + "\n"
