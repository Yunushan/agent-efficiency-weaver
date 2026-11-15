"""Stage and inspect Devin model metadata offline."""

import re
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import (
    _SECRET_KEYS,
    _json_artifact,
    _load_bounded_snapshot,
    _model_id,
    _read_snapshot_json,
    _strings,
    _text,
)

_AIRGAP_FIELDS = {"name", "display_name", "base_url", "api_key", "headers", "api", "capabilities",
                  "max_context_tokens", "max_output_tokens", "default"}
_AIRGAP_CAPABILITIES = {"vision", "reasoning", "parallel_tool_calls"}
_HTTP_HEADER = re.compile(r"[A-Za-z0-9!#$%&'*+.^_|\x60~-]+")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CREDENTIAL_NAMES = (_SECRET_KEYS - {"headers"}) | {"key", "proxy_authorization", "x_api_key", "client_secret", "cookie"}


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _airgap_url(value: object) -> None:
    address = _text(value, "Devin model base URL")
    try:
        parts = urlsplit(address)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise WeaverError("Invalid Devin model base URL") from None
    if (not host or parts.scheme not in {"http", "https"} or parts.username is not None
            or parts.fragment or "\\" in address or any(char.isspace() for char in address)
            or port == 0 or (parts.scheme == "http" and not _loopback(host))):
        raise WeaverError("Devin model base URL must use HTTPS or loopback HTTP without credentials")
    for key, parameter in parse_qsl(parts.query, keep_blank_values=True):
        if key.casefold().replace("-", "_") in _CREDENTIAL_NAMES:
            raise WeaverError("Devin model URL query must contain only non-secret parameters")
        if parameter:
            _text(parameter, "Devin model URL query value")


def _header_name(value: object) -> str:
    name = _text(value, "Devin model HTTP header")
    if not _HTTP_HEADER.fullmatch(name):
        raise WeaverError("Invalid Devin model HTTP header name")
    return name.lower()


def _airgap_headers(api_key: object, headers: object) -> None:
    if not isinstance(api_key, dict) or api_key.keys() - {"env", "header"}:
        raise WeaverError("Devin model api_key requires an environment variable reference")
    env = _text(api_key.get("env"), "Devin model API key environment variable")
    if not _ENVIRONMENT_NAME.fullmatch(env):
        raise WeaverError("Invalid Devin model API key environment variable name")
    auth_header = _header_name(api_key["header"]) if "header" in api_key else "authorization"
    if not isinstance(headers, dict):
        raise WeaverError("Devin model headers must be an object")
    seen = {auth_header}
    for raw_name, value in headers.items():
        name = _header_name(raw_name)
        if name in seen or name.replace("-", "_") in _CREDENTIAL_NAMES:
            raise WeaverError("Duplicate or credential-bearing Devin model HTTP header")
        seen.add(name)
        if value != "":
            _text(value, "Devin model static header value")


def _airgap_model(row: object) -> tuple[str, bool]:
    if not isinstance(row, dict) or row.keys() - _AIRGAP_FIELDS:
        raise WeaverError("Unrecognized Devin air-gapped model fields")
    name = _model_id(row.get("name"))
    if "display_name" in row:
        _text(row["display_name"], "Devin model display name")
    _airgap_url(row.get("base_url"))
    _airgap_headers(row.get("api_key"), row.get("headers", {}))
    api = row.get("api", "chat_completions")
    if api not in ("chat_completions", "responses"):
        raise WeaverError("Unsupported Devin model API protocol")
    _strings(row.get("capabilities", []), "Devin model capabilities", allowed=_AIRGAP_CAPABILITIES)
    context = row.get("max_context_tokens")
    if type(context) is not int or context < 1:
        raise WeaverError("Devin model context window must be a positive integer")
    if "max_output_tokens" in row:
        output = row["max_output_tokens"]
        if type(output) is not int or not 1 <= output <= context:
            raise WeaverError("Devin model output limit must fit within its context window")
    default = row.get("default", False)
    if type(default) is not bool:
        raise WeaverError("Devin model default must be a boolean")
    return name, default


def render_devin_airgap_models(path: Path) -> list[RenderedArtifact]:
    """Validate and stage an operator-authored air-gapped models file."""
    document, _ = _read_snapshot_json(path, "Devin air-gapped models file")
    if not isinstance(document, dict) or document.keys() != {"schema_version", "models"}:
        raise WeaverError("Expected a Devin air-gapped models file with schema_version and models")
    version, models = document["schema_version"], document["models"]
    if type(version) is not int or version != 0 or not isinstance(models, list) or not models:
        raise WeaverError("Expected Devin air-gapped schema version 0 with at least one model")
    names: set[str] = set()
    defaults = 0
    for row in models:
        name, default = _airgap_model(row)
        if name in names:
            raise WeaverError("Duplicate Devin air-gapped model name")
        names.add(name)
        defaults += default
    if defaults > 1:
        raise WeaverError("At most one Devin air-gapped model may be the default")
    return [
        _json_artifact("devin/fragments/airgap/models.json", document, "model-config"),
        RenderedArtifact(PurePosixPath("devin/fragments/airgap/model-setup.md"), (
            "# Configure models for air-gapped Devin\n\n"
            "Use this file with Cognition's separately licensed air-gapped Desktop or CLI build. "
            "Review the endpoints and static headers, and set the named key variables in the agent's process environment.\n\n"
            "Run `devin models validate --file PATH` with the air-gapped CLI, then point "
            "the agent's DEVIN_MODELS_FILE environment variable to the reviewed file's absolute path. "
            "Restart the agent to load the catalog. The reasoning capability declares endpoint support. "
            "Effective reasoning settings and endpoint behavior require a runtime check.\n\n"
            "[Air-gapped Devin models file](https://docs.devin.ai/federal/air-gapped/models-file.md)\n"
        ).encode(), "handoff"),
    ]


def _pricing(value: object) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise WeaverError("Invalid Devin model pricing dimensions")
    dimensions = []
    for entry in value:
        if not isinstance(entry, dict):
            raise WeaverError("Invalid Devin model pricing dimension")
        price = entry.get("value")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
            raise WeaverError("Invalid Devin model price")
        dimension = {
            "label": _text(entry.get("label"), "Devin pricing label"),
            "unit": _text(entry.get("unit"), "Devin pricing unit"),
            "value": price,
            "denominator": _text(entry.get("denominator"), "Devin pricing denominator"),
        }
        if entry.get("info") is not None:
            dimension["info"] = _text(entry["info"], "Devin pricing note")
        dimensions.append(dimension)
    return dimensions


def inspect_devin_models(path: Path, *, model: str | None = None) -> dict[str, Any]:
    """Inspect a saved List Models response and optionally validate an allowed UID."""
    response, digest = _load_bounded_snapshot(path, "Devin Desktop model catalog")
    if not isinstance(response, dict) or not isinstance(response.get("models"), list):
        raise WeaverError("Expected a Devin Desktop List Models response with a models array")
    candidates: dict[str, dict[str, Any]] = {}
    for entry in response["models"]:
        if not isinstance(entry, dict) or type(entry.get("allowed")) is not bool:
            raise WeaverError("Expected a Devin model entry with a boolean allowed flag")
        uid = _model_id(entry.get("uid"))
        if uid in candidates:
            raise WeaverError("Duplicate Devin model UID")
        candidates[uid] = {
            "uid": uid,
            "name": _text(entry.get("name"), "Devin model picker label"),
            "allowed": entry["allowed"],
            "pricing_dimensions": _pricing(entry.get("pricing_dimensions")),
        }
    selected = None
    if model is not None:
        selected = candidates.get(_model_id(model))
        if selected is None or not selected["allowed"]:
            raise WeaverError("Selected model UID is missing or disallowed in the saved Devin Desktop catalog")
    return {
        "schema_version": 1,
        "source": "caller-supplied Devin Desktop GET /api/v2alpha/models response",
        "snapshot_sha256": digest,
        "models": list(candidates.values()),
        "allowed_model_uids": (_strings(response["allowed_model_uids"], "Devin stored allowlist UIDs")
                               if "allowed_model_uids" in response else None),
        "allowed_model_tags": (_strings(response["allowed_model_tags"], "Devin stored allowlist tags")
                               if "allowed_model_tags" in response else None),
        "selection": selected,
        "availability": "unknown",
        "catalog_completeness": "unknown",
    }


def render_devin_model(path: Path, model: str) -> list[RenderedArtifact]:
    """Stage an exact picker reference for a Devin Local session."""
    report = inspect_devin_models(path, model=model)
    selected = report["selection"]
    if sum(row["allowed"] and row["name"] == selected["name"] for row in report["models"]) > 1:
        raise WeaverError("Selected model has an ambiguous picker label in the saved Devin Desktop catalog")
    return [
        _json_artifact("devin/model/model-selection.json", {
            "scope": "manual Devin Local session picker reference",
            "uid": selected["uid"],
            "picker_name": selected["name"],
            "allowed_in_snapshot": selected["allowed"],
            "snapshot_sha256": report["snapshot_sha256"],
        }, "model-selection"),
        RenderedArtifact(PurePosixPath("devin/model/model-setup.md"), (
            "# Select a Devin Local session model\n\n"
            "Open the session model picker in Devin Desktop and choose the exact label in "
            "`model-selection.json`. Check the active model and displayed reasoning variant in the target account.\n\n"
            "The saved catalog records eligibility at capture time. Current account access and installed-client "
            "behavior remain unverified. This handoff applies to the session picker.\n\n"
            "[List Devin Desktop models](https://docs.devin.ai/desktop/accounts/api-reference/list-models)\n"
        ).encode(), "handoff"),
    ]
