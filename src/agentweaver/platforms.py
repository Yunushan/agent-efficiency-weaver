"""Evidence-gated OS workflow diagnostics."""
from __future__ import annotations

import os
import platform
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

SUPPORTED_TARGET_OSES = ("windows", "macos", "linux", "bsd", "solaris", "android", "ios")
_HOST_OSES = (*SUPPORTED_TARGET_OSES, "linux-wsl", "solaris-illumos")
_BSD_NAMES = {
    "freebsd": "bsd-freebsd",
    "openbsd": "bsd-openbsd",
    "netbsd": "bsd-netbsd",
    "dragonfly": "bsd-dragonfly",
    "dragonflybsd": "bsd-dragonfly",
}
_ALIASES = {
    "win": "windows",
    "win32": "windows",
    "cygwin": "windows",
    "msys": "windows",
    "nt": "windows",
    "darwin": "macos",
    "mac": "macos",
    "macosx": "macos",
    "osx": "macos",
    "gnu-linux": "linux",
    "freebsd": "bsd",
    "openbsd": "bsd",
    "netbsd": "bsd",
    "dragonfly": "bsd",
    "dragonflybsd": "bsd",
    "sunos": "solaris",
    "illumos": "solaris",
    "opensolaris": "solaris",
    "termux": "android",
    "ios": "ios",
}
_STATUSES = {"available", "manual", "unsupported", "unknown", "conditional"}
_SUCCESS = {"passed", "pass", "available", "verified", "success", "ok", "working"}
_FAILURE = {"failed", "fail", "error", "broken", "unavailable"}
_NATIVE_KEYS = {"native", "vendor", "vendor_native", "native_vendor", "runtime"}
_ADAPTER_KEYS = {"adapter", "config", "configuration", "export", "exporter", "mcp"}
_EVIDENCE_FIELDS = (
    "os",
    "host_os",
    "target_os",
    "status",
    "result",
    "surface",
    "product",
    "product_version",
    "version",
    "checked_on",
    "artifact_sha256",
    "installed",
    "os_variant",
    "architecture",
    "action",
)
_ROUTE_FIELDS = ("description", "source", "url", "steps", "instructions", "prerequisites", "reason")


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _text(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _flag(value: Any) -> bool:
    return value is True or _text(value) in {"1", "true", "yes", "on"}


def _target_os(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Unknown target OS")
    key = value.strip().lower().replace("_", "-").replace(" ", "-")
    if key in SUPPORTED_TARGET_OSES:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    if key.startswith("bsd-") or key in _BSD_NAMES:
        return "bsd"
    if key.startswith("solaris-"):
        return "solaris"
    raise ValueError("Unknown target OS")


def _host_os(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    key = value.strip().lower().replace("_", "-").replace(" ", "-")
    if key in {"unknown", "other", "none"}:
        return "unknown"
    if key in {"wsl", "wsl2", "linux-wsl"}:
        return "linux-wsl"
    if key in _BSD_NAMES:
        return _BSD_NAMES[key]
    if key.startswith("bsd-"):
        variant = key[4:]
        return _BSD_NAMES.get(variant, "bsd")
    if key in {"illumos", "opensolaris", "solaris-illumos"}:
        return "solaris-illumos"
    if key.startswith("solaris-"):
        return "solaris"
    if key.startswith("android"):
        return "android"
    if key in {"ios", "iphoneos"}:
        return "ios"
    if key in _ALIASES:
        canonical = _ALIASES[key]
        if canonical in _BSD_NAMES.values():
            return canonical
        return canonical
    if key in _HOST_OSES:
        return key
    return "unknown"


def _os_parts(os_name: str) -> tuple[str, str | None]:
    if os_name.startswith("bsd-"):
        return "bsd", os_name[4:]
    if os_name == "solaris-illumos":
        return "solaris", "illumos"
    if os_name == "linux-wsl":
        return "linux", "wsl"
    return os_name, None


def _read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    result: dict[str, str] = {}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def collect_host_facts() -> dict[str, Any]:
    release = platform.release()
    version = platform.version()
    sys_platform = sys.platform
    os_release = _read_os_release()
    return {
        "system": platform.system(),
        "release": release,
        "version": version,
        "machine": platform.machine(),
        "sys_platform": sys_platform,
        "os_release_id": os_release.get("ID", ""),
        "os_release_version_id": os_release.get("VERSION_ID", ""),
        "wsl": bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP")),
        "android": bool(
            os.environ.get("ANDROID_ROOT")
            or os.environ.get("ANDROID_DATA")
            or "com.termux" in os.environ.get("PREFIX", "")
        ),
        "termux": bool(os.environ.get("TERMUX_VERSION") or "com.termux" in os.environ.get("PREFIX", "")),
        "ios": sys_platform == "ios",
        "illumos": "illumos" in f"{release} {version}".lower(),
    }


def detect_host_os(facts: Mapping[str, Any] | None = None, **overrides: Any) -> str:
    if facts is None:
        data: dict[str, Any] = collect_host_facts()
    elif isinstance(facts, Mapping):
        data = dict(facts)
    else:
        raise TypeError("OS facts must be a mapping")
    data.update(overrides)
    system = _text(data.get("system"))
    sys_platform = _text(data.get("sys_platform"))
    release = str(data.get("release", ""))
    version = str(data.get("version", ""))
    os_release_id = _text(data.get("os_release_id"))
    prefix = str(data.get("prefix", ""))
    markers = f"{sys_platform} {release} {version} {os_release_id} {prefix}".lower()
    if (
        _flag(data.get("android"))
        or sys_platform == "android"
        or os_release_id == "android"
        or _flag(data.get("termux"))
        or "com.termux" in prefix.lower()
        or "android" in markers
    ):
        return "android"
    if _flag(data.get("ios")) or sys_platform == "ios" or os_release_id == "ios":
        return "ios"
    if system in {"win32", "win64", "windows"}:
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        wsl = (
            _flag(data.get("wsl"))
            or "microsoft" in release.lower()
            or "wsl" in release.lower()
            or "wsl" in sys_platform
        )
        return "linux-wsl" if wsl else "linux"
    if system in _BSD_NAMES:
        return _BSD_NAMES[system]
    if system == "bsd":
        return "bsd"
    if system == "sunos":
        illumos = _flag(data.get("illumos")) or "illumos" in f"{release} {version}".lower()
        return "solaris-illumos" if illumos else "solaris"
    return "unknown"


def _surface(agent: Any, surface_id: str | None) -> Any:
    surfaces = _get(agent, "surfaces", {})
    default = _get(agent, "default_surface", None)
    requested = surface_id or default
    if isinstance(surfaces, Mapping):
        if requested is None:
            if len(surfaces) == 1:
                return next(iter(surfaces.values()))
            raise ValueError("Surface is required")
        if requested in surfaces:
            return surfaces[requested]
        for value in surfaces.values():
            if _get(value, "id") == requested:
                return value
        raise ValueError("Unknown surface")
    if isinstance(surfaces, Sequence) and not isinstance(surfaces, (str, bytes)):
        if requested is None:
            if len(surfaces) == 1:
                return surfaces[0]
            raise ValueError("Surface is required")
        for value in surfaces:
            if _get(value, "id") == requested:
                return value
        raise ValueError("Unknown surface")
    if requested is not None:
        raise ValueError("Unknown surface")
    return None


def _workflow_entry(workflows: Any, target: str) -> Any:
    if not workflows:
        return None
    candidates = [target, _os_parts(target)[0]]
    if isinstance(workflows, Mapping):
        for candidate in candidates:
            if candidate in workflows:
                return workflows[candidate]
        return None
    if isinstance(workflows, Sequence) and not isinstance(workflows, (str, bytes)):
        for value in workflows:
            declared = _get(value, "target_os") or _get(value, "os") or _get(value, "family")
            try:
                if declared is not None and _target_os(declared) == target:
                    return value
            except ValueError:
                continue
    return None


def _status(value: Any) -> str | None:
    status = _text(value)
    if status in _STATUSES:
        return status
    if status in {"true", "supported", "enabled", "ready"}:
        return "available"
    if status in {"false", "disabled", "not-supported", "unavailable"}:
        return "unsupported"
    return None


def _evidence_value(surface: Any, names: set[str]) -> Any:
    for field in ("evidence", "capabilities"):
        evidence = _get(surface, field, {})
        if isinstance(evidence, Mapping):
            for name, value in evidence.items():
                if _text(name) in names:
                    return value
        elif isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)):
            for value in evidence:
                name = _text(_get(value, "capability") or _get(value, "name") or _get(value, "id"))
                if name in names:
                    return value
    return None


def _adapter(surface: Any, entry: Any) -> dict[str, Any]:
    declared = _get(entry, "adapter") or _get(entry, "adapter_config")
    status = _status(_get(declared, "status")) if declared is not None else None
    if status is None:
        evidence = _evidence_value(surface, _ADAPTER_KEYS)
        status = _status(_get(evidence, "state")) or _status(_get(evidence, "status"))
    dialect = _get(surface, "mcp_dialect")
    path = _get(surface, "native_path_hint")
    scope = _get(surface, "config_scope")
    if status is None:
        if isinstance(dialect, str) and dialect.strip().lower() not in {"", "none", "manual", "unsupported"}:
            status = "available" if path or scope else "unknown"
        elif _get(surface, "instruction_mode") not in (None, "", "none", "unsupported"):
            status = "manual"
        else:
            status = "unknown"
    reason = (
        "A configuration adapter can stage artifacts; this is not installation or runtime verification."
        if status == "available"
        else "No verified native configuration adapter is declared for this surface."
    )
    return {
        "status": status,
        "installed": False,
        "runtime_verified": False,
        "surface": _get(surface, "id"),
        "config_scope": scope if isinstance(scope, str) else None,
        "config_path_hint": path if isinstance(path, str) else None,
        "mcp_dialect": dialect if isinstance(dialect, str) else None,
        "reason": reason,
    }


def _append_checks(target: list[dict[str, Any]], source: Any) -> None:
    if isinstance(source, Mapping):
        checks = _get(source, "runtime_checks")
        if isinstance(checks, Sequence) and not isinstance(checks, (str, bytes)):
            for check in checks:
                if isinstance(check, Mapping):
                    target.append(dict(check))
        elif any(key in source for key in ("os", "target_os", "host_os")) and any(key in source for key in ("status", "result")):
            target.append(dict(source))
    elif isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        for value in source:
            _append_checks(target, value)


def _runtime_checks(surface: Any, entry: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _append_checks(checks, _get(surface, "runtime_checks"))
    _append_checks(checks, _get(entry, "runtime_checks"))
    _append_checks(checks, _get(entry, "native"))
    evidence = _get(surface, "evidence", {})
    if isinstance(evidence, Mapping):
        for name, value in evidence.items():
            if _text(name) in _NATIVE_KEYS:
                _append_checks(checks, value)
    else:
        _append_checks(checks, evidence)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for check in checks:
        key = tuple(sorted((name, repr(value)) for name, value in check.items()))
        if key not in seen:
            seen.add(key)
            unique.append(check)
    return unique


def _evidence_os(value: Any) -> str:
    text = _text(value)
    if not text:
        return "unknown"
    if text in _BSD_NAMES:
        return _BSD_NAMES[text]
    if text in {"illumos", "opensolaris"}:
        return "solaris-illumos"
    return _host_os(text)


def _check_matches(check_os: str, target: str, host: str) -> bool:
    check_family, check_qualifier = _os_parts(check_os)
    host_family, host_qualifier = _os_parts(host)
    if check_family != target:
        return False
    if target == "bsd":
        return bool(check_qualifier) and check_qualifier == host_qualifier
    if target == "solaris":
        return (check_os == "solaris-illumos") == (host_qualifier == "illumos")
    if target == "linux":
        return check_qualifier is None
    return True


def _architecture(value: Any) -> str:
    name = _text(value)
    return {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(name, name)


def _host_variant(host: str) -> str | None:
    """Name the actual OS variant without hostnames, paths or a vendor lookup."""
    facts = collect_host_facts()
    if host == "unknown" or detect_host_os(facts) != host:
        return None
    family, _ = _os_parts(host)
    if family == "linux":
        name, version = facts.get("os_release_id"), facts.get("os_release_version_id")
        return f"{host}:{name}:{version}" if name and version else None
    if family == "macos":
        version = platform.mac_ver()[0]
        return f"macos:{version}" if version else None
    release = facts.get("release")
    if family == "windows":
        version = facts.get("version")
        return f"windows:{release}:{version}" if release and version else None
    return f"{host}:{release}" if release else None


def _complete_native_check(check: Mapping[str, Any], product: str, surface: str) -> bool:
    """A status string or another client's result cannot establish consumption."""
    if not isinstance(product, str) or not product or not isinstance(surface, str) or not surface:
        return False
    if check.get("product") != product or check.get("surface") != surface:
        return False
    if any(not isinstance(check.get(key), str) or not check[key].strip()
           for key in ("product_version", "os_variant", "architecture", "checked_on")):
        return False
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+() -]{0,199}", check["product_version"]) or
            not re.search(r"\d", check["product_version"]) or
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", check["checked_on"])):
        return False
    if check.get("action") != "configuration-consumption" or type(check.get("installed")) is not bool:
        return False
    if not isinstance(check.get("artifact_sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", check["artifact_sha256"]):
        return False
    try:
        date.fromisoformat(check["checked_on"])
    except ValueError:
        return False
    status = _text(check.get("status") or check.get("result"))
    return status not in _SUCCESS or check["installed"] is True


def _native(surface: Any, entry: Any, target: str, host: str, product: str) -> dict[str, Any]:
    workflow_state = _status(entry) if isinstance(entry, str) else _status(_get(entry, "state"))
    checks = _runtime_checks(surface, entry)
    host_variant = _host_variant(host)
    architecture = _architecture(platform.machine())
    matching: list[dict[str, Any]] = []
    for check in checks:
        if not _complete_native_check(check, product, _get(surface, "id")):
            continue
        if (not host_variant or check["os_variant"] != host_variant or not architecture or
                _architecture(check["architecture"]) != architecture):
            continue
        scopes = {_evidence_os(check[field]) for field in ("target_os", "os", "host_os") if field in check}
        if len(scopes) != 1 or "unknown" in scopes:
            continue
        check_os = next(iter(scopes))
        if _check_matches(check_os, target, host):
            sanitized = {name: check[name] for name in _EVIDENCE_FIELDS if name in check}
            sanitized["os"] = check_os
            sanitized["status"] = _text(check.get("status") or check.get("result")) or "unknown"
            matching.append(sanitized)
    successes = [check for check in matching if check["status"] in _SUCCESS]
    failures = [check for check in matching if check["status"] in _FAILURE]
    installed_values = {check["installed"] for check in matching if isinstance(check.get("installed"), bool)}
    if successes and len(successes) == len(matching):
        status = "available"
        available: bool | None = True
        reason = "Matching configuration-consumption evidence covers the recorded product version, OS variant and architecture."
    elif failures and not successes:
        status = "unsupported"
        available = False
        reason = "Matching runtime evidence reports that the vendor-native route is unavailable."
    elif successes:
        status = "unknown"
        available = None
        reason = "Conflicting matching runtime observations must be resolved before availability can be established."
    elif workflow_state == "unsupported":
        status = "unsupported"
        available = False
        reason = "The documented OS workflow marks vendor-native use as unsupported."
    else:
        status = "unknown"
        available = None
        reason = "No matching vendor runtime evidence; documentation and catalog entries do not prove availability."
    return {
        "status": status,
        "available": available,
        "installed": next(iter(installed_values)) if len(installed_values) == 1 else None,
        "target_os": target,
        "documented_state": workflow_state,
        "host_variant": host_variant,
        "host_architecture": architecture or None,
        "evidence": matching,
        "reason": reason,
    }


def _route(entry: Any, name: str) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "status": "unknown",
        "description": None,
        "source": None,
        "steps": [],
        "prerequisites": [],
        "reason": "No documented route in the OS workflow catalog.",
    }
    if isinstance(entry, str):
        state = _status(entry)
        if name == "manual" and state == "manual":
            return {
                **empty,
                "status": "manual",
                "reason": "The catalog documents a manual OS workflow without a native runtime claim.",
            }
        if state == "unsupported":
            return {**empty, "status": "unsupported", "reason": "The catalog marks this OS workflow unsupported."}
        return empty
    value = _get(entry, name)
    if value is None:
        value = _get(entry, f"{name}_route")
    routes = _get(entry, "routes")
    if value is None and isinstance(routes, Mapping):
        value = routes.get(name)
    if value is None:
        return empty
    if isinstance(value, bool):
        return {**empty, "status": "available" if value else "unsupported", "reason": None}
    if isinstance(value, str):
        status = _status(value)
        return {
            **empty,
            "status": status or "available",
            "description": None if status else value,
            "reason": None if status else empty["reason"],
        }
    status = _status(_get(value, "status") or _get(value, "availability") or _get(value, "mode"))
    if status is None:
        supported = _get(value, "supported")
        status = "available" if supported is True else "unsupported" if supported is False else "available"
    raw_steps = _get(value, "steps") or _get(value, "instructions")
    if isinstance(raw_steps, str):
        steps = [raw_steps]
    elif isinstance(raw_steps, Sequence) and not isinstance(raw_steps, (str, bytes)):
        steps = [item for item in raw_steps if isinstance(item, str)]
    else:
        steps = []
    raw_prerequisites = _get(value, "prerequisites")
    if isinstance(raw_prerequisites, str):
        prerequisites = [raw_prerequisites]
    elif isinstance(raw_prerequisites, Sequence) and not isinstance(raw_prerequisites, (str, bytes)):
        prerequisites = [item for item in raw_prerequisites if isinstance(item, str)]
    else:
        prerequisites = []
    description = _get(value, "description")
    source = _get(value, "source") or _get(value, "url")
    reason = _get(value, "reason")
    return {
        "status": status,
        "description": description if isinstance(description, str) else None,
        "source": source if isinstance(source, str) else None,
        "steps": steps,
        "prerequisites": prerequisites,
        "reason": reason if isinstance(reason, str) else None,
    }


def _host_matches(target: str, host: str) -> tuple[bool, bool]:
    family, qualifier = _os_parts(host)
    if host == "unknown" or family != target:
        return False, False
    if target in {"bsd", "solaris"}:
        return True, qualifier is not None
    return True, qualifier is None


def _core_runtime(target: str) -> dict[str, Any]:
    supported = sys.version_info >= (3, 11)
    if target in {"windows", "macos", "linux"}:
        status = "eligible" if supported else "unsupported"
        eligible = supported
    elif target in {"bsd", "solaris", "android"}:
        status = "conditional" if supported else "unsupported"
        eligible = supported
    elif target == "ios":
        status = "unsupported"
        eligible = False
    else:
        status = "unknown"
        eligible = None
    return {
        "status": status,
        "eligible": eligible,
        "minimum_python": "3.11",
        "python_version": platform.python_version(),
        "scope": "target_requirement",
        "requires": ["Python 3.11+"]
        + (["Real target-host runtime check"] if target in {"bsd", "solaris"} else [])
        + (["Termux or compatible Python environment"] if target == "android" else [])
        + (["A separate supported host with a vendor-documented remote interface"] if target == "ios" else []),
    }


def _pending(native: dict[str, Any], target: str, host: str, matches: bool, core: dict[str, Any]) -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    if native["status"] == "unknown":
        pending.append(
            {
                "capability": "native_vendor_runtime",
                "target_os": target,
                "status": "pending",
                "reason": "Installed vendor-agent execution has not been verified on the target OS.",
            }
        )
    if matches is False:
        pending.append(
            {
                "capability": "native_host_runtime",
                "target_os": target,
                "host_os": host,
                "status": "pending",
                "reason": "The requested target differs from the current host or its runtime qualifier.",
            }
        )
    if core["status"] == "conditional":
        pending.append(
            {
                "capability": "core_runtime_real_host",
                "target_os": target,
                "status": "pending",
                "reason": "Core CLI eligibility is conditional and requires a real-host check.",
            }
        )
    if target == "ios":
        pending.append(
            {
                "capability": "on_device_runtime",
                "target_os": target,
                "status": "unsupported",
                "reason": "This release uses a separate host and remote handoff, not an on-device CLI.",
            }
        )
    return pending


def describe_workflow(
    agent: Any,
    target_os: str,
    host_os: str,
    *,
    surface_id: str | None = None,
) -> dict[str, Any]:
    target = _target_os(target_os)
    host = _host_os(host_os)
    surface = _surface(agent, surface_id)
    selected_surface = _get(surface, "id", surface_id or _get(agent, "default_surface"))
    workflows = _get(surface, "os_workflows", None)
    if not workflows:
        workflows = _get(agent, "os_workflows", {})
    entry = _workflow_entry(workflows, target)
    adapter = _adapter(surface, entry)
    native = _native(surface, entry, target, host, _get(agent, "id"))
    manual = _route(entry, "manual")
    remote = _route(entry, "remote")
    core = _core_runtime(target)
    matches, qualifier_match = _host_matches(target, host)
    workflow_state = _status(entry) if isinstance(entry, str) else _status(_get(entry, "state"))
    if workflow_state is None:
        workflow_state = _status(_get(entry, "status"))
    declared_mode = _text(_get(entry, "mode") or _get(entry, "workflow"))
    if declared_mode:
        mode = declared_mode
    elif native["status"] == "available":
        mode = "native"
    elif workflow_state == "unsupported":
        mode = "unsupported"
    elif remote["status"] == "available":
        mode = "remote"
    elif manual["status"] in {"available", "manual"} or workflow_state == "manual":
        mode = "manual"
    elif workflow_state == "available":
        mode = "documented"
    elif adapter["status"] == "available" and entry is not None:
        mode = "staged-config"
    else:
        mode = "unknown"
    strict_ready = bool(
        native["status"] == "available"
        and native["evidence"]
        and matches
        and qualifier_match
        and core["eligible"] is True
        and adapter["status"] == "available"
        and mode not in {"remote", "unknown", "unsupported"}
    )
    warnings: list[str] = []
    host_family, host_qualifier = _os_parts(host)
    if host == "unknown":
        warnings.append("Host OS could not be identified; native runtime evidence cannot be established.")
    elif not matches:
        warnings.append(f"Host {host} does not match target {target}.")
    if host_qualifier == "wsl":
        warnings.append("WSL is a Linux environment and is not verified native Windows or plain Linux support.")
    if target == "bsd":
        warnings.append("BSD evidence must name the actual variant; it does not validate other BSD variants.")
    if target == "ios" and remote["status"] != "available":
        warnings.append("iOS requires a separately supported host and vendor-documented remote interface.")
    pending = _pending(native, target, host, matches, core)
    return {
        "schema_version": 1,
        "agent": {
            "id": _get(agent, "id"),
            "name": _get(agent, "name"),
            "kind": _get(agent, "kind"),
            "surface": selected_surface,
            "default_surface": _get(agent, "default_surface"),
        },
        "target": {
            "os": target,
            "family": _os_parts(target)[0],
            "qualifier": _os_parts(target)[1],
        },
        "host": {
            "os": host,
            "family": host_family,
            "qualifier": host_qualifier,
            "matches_target": matches,
            "qualifier_match": qualifier_match,
        },
        "core_runtime": core,
        "adapter": adapter,
        "native_vendor": native,
        "workflow": {
            "mode": mode,
            "on_device_cli": core["eligible"] is True and core["status"] == "eligible",
            "core_cli_eligibility": core["status"],
            "requires_separate_host": target == "ios",
        },
        "manual_route": manual,
        "remote_route": remote,
        "prerequisites": list(dict.fromkeys(manual["prerequisites"] + remote["prerequisites"] + core["requires"])),
        "pending_evidence": pending,
        "warnings": warnings,
        "strict_ready": strict_ready,
    }
