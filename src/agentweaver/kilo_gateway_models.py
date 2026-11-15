"""Inspect saved Kilo Gateway catalogs and stage exact advertised selections."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters import json_bytes
from .artifacts import RenderedArtifact
from .config import WeaverError
from .models import _EFFORTS, _model_id, _strings, _text, read_model_snapshot
from .opencode_models import _variant_id, kilo_run_arguments

KILO_GATEWAY_CATALOG = "https://kilo.ai/docs/gateway/models-and-providers"
KILO_GATEWAY_CLI = "https://kilo.ai/docs/code-with-ai/platforms/cli-reference#kilo-run"


def omit_catalog_descriptions(raw: dict[str, Any]) -> dict[str, Any]:
    """Discard public prose before scanning; validate structured description values as usual."""
    def without_prose(page: Any) -> Any:
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            return page
        return {**page, "data": [
            {key: value for key, value in entry.items() if key != "description"}
            if isinstance(entry, dict) and isinstance(entry.get("description"), str) else entry
            for entry in page["data"]
        ]}

    filtered = without_prose(raw)
    if isinstance(raw.get("pages"), list):
        filtered["pages"] = [without_prose(page) for page in raw["pages"]]
    return filtered


def _variant(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"reasoning", "verbosity"}:
        raise WeaverError("Unsupported Kilo Gateway variant settings")
    reasoning = value.get("reasoning")
    if not isinstance(reasoning, dict) or set(reasoning) != {"enabled", "effort"}:
        raise WeaverError("Invalid Kilo Gateway variant reasoning settings")
    effort = _text(reasoning["effort"], "Kilo Gateway variant effort")
    if type(reasoning["enabled"]) is not bool or effort not in _EFFORTS:
        raise WeaverError("Invalid Kilo Gateway variant reasoning settings")
    fields: dict[str, Any] = {"reasoning": {"enabled": reasoning["enabled"], "effort": effort}}
    if "verbosity" in value:
        fields["verbosity"] = _text(value["verbosity"], "Kilo Gateway variant verbosity")
    return fields


def _controls(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WeaverError("Kilo Gateway reasoning options must be an array")
    controls: dict[str, dict[str, Any]] = {}
    for option in value:
        if not isinstance(option, dict):
            raise WeaverError("Invalid Kilo Gateway reasoning option")
        kind = _text(option.get("type"), "Kilo Gateway reasoning option type")
        if kind in controls:
            raise WeaverError("Duplicate Kilo Gateway reasoning option type")
        if kind == "effort" and set(option) == {"type", "values"}:
            values = _strings(option["values"], "Kilo Gateway reasoning efforts", allowed=_EFFORTS)
            if not values:
                raise WeaverError("Kilo Gateway effort option requires values")
            controls[kind] = {"type": kind, "values": values}
        elif kind == "toggle" and set(option) == {"type"}:
            controls[kind] = {"type": kind}
        else:
            raise WeaverError("Unsupported Kilo Gateway reasoning option")
    return list(controls.values())


def kilo_gateway_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    """Keep the gateway's declared controls and variant settings as catalog metadata."""
    metadata = entry.get("opencode", {})
    if not isinstance(metadata, dict):
        raise WeaverError("Invalid Kilo Gateway client metadata")
    variants = metadata.get("variants", {})
    if not isinstance(variants, dict):
        raise WeaverError("Kilo Gateway variants must be an object")
    report: dict[str, Any] = {
        "variants": {_variant_id(name, "Kilo Gateway"): _variant(value) for name, value in variants.items()},
        "reasoning_options": _controls(metadata.get("reasoning_options", [])),
    }
    if metadata.get("ai_sdk_provider") is not None:
        report["ai_sdk_provider"] = _text(metadata["ai_sdk_provider"], "Kilo Gateway client SDK")
    if entry.get("alias_target") is not None:
        report["alias_target"] = _model_id(entry["alias_target"])
    return report


def select_kilo_gateway_model(
    catalog: Mapping[str, Any], model: str, variant: str | None = None, *, for_client: bool = False,
) -> dict[str, Any]:
    """Select an exact model and its advertised options from a normalized catalog."""
    if catalog.get("provider_id") != "kilo-gateway":
        raise WeaverError("Expected a normalized Kilo Gateway model catalog")
    model = _model_id(model)
    entry = next((row for row in catalog["models"] if row["id"] == model), None)
    if entry is None:
        raise WeaverError("The saved Kilo Gateway catalog does not list the exact model ID")
    if for_client:
        # Kilo's Gateway loader filters models that explicitly omit the tools parameter.
        parameters = entry.get("supported_parameters")
        if parameters and "tools" not in parameters:
            raise WeaverError("Kilo clients require tool calling from the selected Gateway model")
        output = entry.get("architecture", {}).get("output_modalities")
        if output is not None and "text" not in output:
            raise WeaverError("The selected Gateway model must support text output in Kilo chat")
    selection: dict[str, Any] = {"model": model}
    if variant is not None:
        name = _variant_id(variant, "Kilo Gateway")
        variants = entry["kilo_gateway"]["variants"]
        if name not in variants:
            raise WeaverError("The selected Kilo Gateway model does not advertise that variant")
        selection["variant"] = name
        selection["advertised_variant_settings"] = variants[name]
    return selection


def render_kilo_gateway_selection(
    root: Path, path: Path, model: str, variant: str | None = None, *, surface: str = "gateway",
) -> list[RenderedArtifact]:
    """Stage a catalog-checked selector and the vendor's advertised variant metadata."""
    catalog = read_model_snapshot(path, "kilo-gateway", root)
    for_client = surface != "gateway"
    selection = select_kilo_gateway_model(catalog, model, variant, for_client=for_client)
    prefix = PurePosixPath("kilo/model" if for_client else "kilo/gateway")
    selection_path = "gateway-selection.json" if for_client else "model-selection.json"
    client_model = f"kilo/{selection['model']}"
    if for_client:
        selection["client_model"] = client_model
    if surface == "cli":
        instructions = "Use the staged argument array with the Kilo CLI and supply your prompt separately."
    elif for_client:
        instructions = "Choose the listed model and variant from the configured Kilo Gateway provider in the chat."
    else:
        instructions = (
            "Choose the listed model in the configured Kilo Gateway provider. For the CLI, use the model ID with "
            "the `kilo/` prefix and pass the exact named variant with `--variant=NAME` when one is selected."
        )
    note = (
        "# Kilo Gateway model selection\n\n"
        f"{instructions} Confirm the model and variant in your active session.\n\n"
        "The JSON records catalog metadata. Kilo routes the variant through its chosen request protocol. "
        "Account availability and live reasoning require separate verification.\n\n"
        f"Saved Kilo Gateway catalog SHA-256: {catalog['provenance']['sha256']}\n\n"
        f"- {KILO_GATEWAY_CATALOG}\n- {KILO_GATEWAY_CLI}\n"
    )
    artifacts = [
        RenderedArtifact(prefix / selection_path, json_bytes(selection), "model-selection"),
        RenderedArtifact(prefix / "model-setup.md", note.encode("utf-8"), "model-setup"),
    ]
    if surface == "cli":
        argv = kilo_run_arguments(client_model, selection.get("variant"))
        artifacts.append(RenderedArtifact(prefix / "session-arguments.json", json_bytes(argv), "session-arguments"))
    return artifacts
