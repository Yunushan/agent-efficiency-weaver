import json
import os
import socket
import tempfile
import tomllib
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import yaml

from agentweaver.config import WeaverError
from agentweaver.integrations import get_integration
from agentweaver.models import (
    check_model,
    load_model_providers,
    model_inventory,
    read_model_snapshot,
    read_openrouter_provider_endpoints,
    render_model_setup,
)

ROOT = Path(__file__).resolve().parents[1]


class ModelCatalogTests(unittest.TestCase):
    def test_all_provider_routes_reference_registry_surfaces(self) -> None:
        providers = load_model_providers(ROOT)
        self.assertEqual(set(providers), {"openrouter", "kilo-gateway", "openai", "anthropic", "google", "lm-studio",
                                          "perplexity-agent", "perplexity-orchestrator"})
        for item in providers.values():
            get_integration(item["integration_id"], ROOT).surface(item["surface"])
            self.assertTrue(item["sources"])
            self.assertTrue(item["discovery"]["auth"])

    def test_inventory_does_not_claim_exhaustiveness_or_inference(self) -> None:
        inventory = model_inventory(ROOT)
        self.assertEqual(inventory["catalog_completeness"], "unknown")
        self.assertFalse(inventory["inference_tested"])
        self.assertFalse(inventory["network_used"])

    def test_catalog_rejects_unregistered_surface_and_duplicate_provider(self) -> None:
        for change in ("surface", "duplicate"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "catalogs").mkdir()
                integrations = (ROOT / "catalogs/integrations.json").read_bytes()
                (root / "catalogs/integrations.json").write_bytes(integrations)
                catalog = json.loads((ROOT / "catalogs/model-providers.json").read_text())
                if change == "surface":
                    catalog["providers"][0]["surface"] = "invented"
                else:
                    catalog["providers"].append(catalog["providers"][0])
                (root / "catalogs/model-providers.json").write_text(json.dumps(catalog))
                with self.assertRaises(WeaverError):
                    load_model_providers(root)


class SnapshotTests(unittest.TestCase):
    def read(self, provider: str, value: object) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return read_model_snapshot(path, provider, ROOT)

    def test_openrouter_provider_endpoint_metadata_is_scoped_to_one_model(self) -> None:
        model = "fixture/model"
        entry = {"model_id": model, "tag": "fixture/fp4/us/fast", "provider_name": "Fixture",
                 "context_length": 8192, "max_completion_tokens": 1024, "max_prompt_tokens": None,
                 "status": -2, "supported_parameters": ["reasoning", "tools"]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "endpoints.json"
            path.write_text(json.dumps({"data": {"id": model, "endpoints": [entry]}}), encoding="utf-8")
            snapshot = read_openrouter_provider_endpoints(path)
            for changes in ({"model_id": "fixture/other"}, {"tag": "../escape"}, {"status": False},
                            {"supported_parameters": ["reasoning", "reasoning"]}, {"context_length": -1},
                            {"headers": {"Authorization": "fixture"}}):
                with self.subTest(changes=changes), self.assertRaises(WeaverError):
                    path.write_text(json.dumps({"data": {"id": model, "endpoints": [entry | changes]}}), encoding="utf-8")
                    read_openrouter_provider_endpoints(path)
        self.assertEqual(snapshot["model_id"], model)
        self.assertEqual(snapshot["endpoint_count"], 1)
        self.assertEqual(snapshot["provider_endpoints"][0], {
            "tag": entry["tag"], "provider_name": "Fixture", "context_length": 8192,
            "max_completion_tokens": 1024, "status": -2, "supported_parameters": ["reasoning", "tools"],
        })
        self.assertEqual(snapshot["availability"], "unknown")
        self.assertFalse(snapshot["inference_tested"])

    def test_openrouter_provider_options_stage_parameter_filter_on_the_two_gateway_routes(self) -> None:
        from tests.test_cli_exports import invoke

        model = "fixture/reasoner"
        provider_id = "openrouter"
        with tempfile.TemporaryDirectory() as directory:
            model_list = Path(directory) / "models.json"
            endpoints = Path(directory) / "provider-endpoints.json"
            model_list.write_text(json.dumps({"data": [{"id": model, "supported_parameters": ["reasoning"],
                                                        "reasoning": {"supported_efforts": ["high"]}}]}), encoding="utf-8")
            endpoints.write_text(json.dumps({"data": {"id": model, "endpoints": [
                {"model_id": model, "tag": "fixture/us", "provider_name": "Fixture", "supported_parameters": ["reasoning"]},
                {"model_id": model, "tag": "fixture/eu", "provider_name": "Fixture", "supported_parameters": []},
            ]}}), encoding="utf-8")
            common = ("--root", str(ROOT), "--provider", provider_id, "--openrouter-provider-endpoints", str(endpoints))
            code, output, error = invoke("models", *common)
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["endpoint_count"], 2)
            launch = ("model", *common, "--agent", "openrouter", "--surface", "gateway", "--catalog", str(model_list),
                      "--model", model, "--reasoning-effort", "high", "--dry-run")
            for protocol in ("openrouter-chat", "openai-responses"):
                with self.subTest(protocol=protocol):
                    code, output, error = invoke(*launch, "--protocol", protocol, "--openrouter-provider-tag", "fixture/us")
                    self.assertEqual((code, error), (0, ""))
                    selection, = (item for item in json.loads(output)["artifacts"] if item["kind"] == "model-selection")
                    self.assertEqual(json.loads(selection["content"]), {
                        "model": model, "reasoning": {"effort": "high"},
                        "provider": {"only": ["fixture/us"], "require_parameters": True},
                    })
            for tag, error_text in (("fixture/eu", "does not advertise the reasoning"),
                                    ("fixture", "requested exact provider tag")):
                code, _, error = invoke(*launch, "--openrouter-provider-tag", tag)
                self.assertEqual(code, 2)
                self.assertIn(error_text, error)
            for arguments, expected in (((), "requires a tag"),
                                        (("--openrouter-provider-tag", "fixture/us", "--protocol", "openrouter-messages"),
                                         "requires gateway Chat Completions or Responses")):
                code, _, error = invoke(*launch, *arguments)
                self.assertEqual(code, 2)
                self.assertIn(expected, error)
            code, _, error = invoke("model", *common, "--agent", "openrouter", "--model", "fixture/other",
                                    "--openrouter-provider-tag", "fixture/us", "--dry-run")
            self.assertEqual(code, 2)
            self.assertIn("is for a different model", error)

    def test_openrouter_cli_stages_a_history_item_without_replacing_baseline_effort(self) -> None:
        from tests.test_cli_exports import invoke

        model = "openai/gpt-6.1-sol"
        cases = (
            ("openrouter-chat", "chat/completions", {"reasoning": {"effort": "high"}},
             {"role": "system", "content": "", "configuration_update": {"reasoning": {"effort": "low"}}}),
            ("openai-responses", "responses", {"reasoning": {"effort": "high"}},
             {"type": "configuration_update", "reasoning": {"effort": "low"}}),
            ("openrouter-messages", "messages", {"output_config": {"effort": "high"}},
             {"role": "system", "content": [], "output_config": {"effort": "low"}}),
        )
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "models.json"
            catalog.write_text(json.dumps({"data": [{"id": model,
                "endpoints": [f"/api/v1/{case[1]}" for case in cases],
                "reasoning": {"supported_efforts": ["low", "high"]},
            }]}), encoding="utf-8")
            for protocol, _, baseline, history_item in cases:
                with self.subTest(protocol=protocol):
                    code, output, error = invoke(
                        "model", "--root", str(ROOT), "--agent", "openrouter", "--provider", "openrouter",
                        "--model", model, "--catalog", str(catalog), "--protocol", protocol,
                        "--reasoning-effort", "high", "--openrouter-turn-effort", "low", "--dry-run",
                    )
                    self.assertEqual((code, error), (0, ""))
                    artifacts = {item["kind"]: item for item in json.loads(output)["artifacts"]}
                    self.assertEqual(json.loads(artifacts["model-selection"]["content"]), {"model": model, **baseline})
                    self.assertEqual(json.loads(artifacts["conversation-update"]["content"]), history_item)
                    self.assertIn("immediately before the user turn", artifacts["model-setup"]["content"])

    def test_openrouter_turn_effort_requires_known_model_endpoint_and_effort(self) -> None:
        spec = get_integration("openrouter", ROOT)
        model = "anthropic/claude-opus-5"
        entry = {"id": model, "endpoints": ["/api/v1/chat/completions", "/api/v1/responses", "/api/v1/messages"],
                 "reasoning": {"supported_efforts": ["none", "low", "high"]}}
        catalog = self.read("openrouter", {"data": [entry]})
        common = {"root": ROOT, "provider_id": "openrouter", "model_id": model, "snapshot": catalog,
                  "openrouter_turn_effort": "low"}
        for changes, error in (
            ({"model_id": "anthropic/claude-opus-5.5"}, "support for this exact model"),
            ({"model_id": "openai/gpt-6-astra-pro"}, "support for this exact model"),
            ({"snapshot": None}, "catalog that lists the model and endpoint"),
            ({"snapshot": self.read("openrouter", {"data": [entry | {"endpoints": []}]})},
             "does not advertise"),
            ({"openrouter_turn_effort": "xhigh"}, "effort is unsupported"),
            ({"openrouter_turn_effort": "none"}, "Claude does not accept none"),
            ({"mode": "pro"}, "cannot be combined with pro mode"),
            ({"protocol": "openrouter-batch"}, "requires OpenRouter gateway"),
            ({"provider_id": "openai"}, "requires OpenRouter gateway"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(WeaverError, error):
                render_model_setup(spec, "gateway", **(common | changes))
        artifacts = render_model_setup(
            spec, "gateway", protocol="openrouter-messages", **(common | {"openrouter_turn_effort": "minimal"}),
        )
        update, = [item for item in artifacts if item.kind == "conversation-update"]
        self.assertEqual(json.loads(update.content), {
            "role": "system", "content": [], "output_config": {"effort": "low"},
        })

    def test_openrouter_conditional_pricing_keeps_order_conditions_and_exact_decimals(self) -> None:
        pricing = {"prompt": "0.000004", "completion": "0.00002", "input_cache_write_1h": "0.000008", "discount": 0.1,
                   "overrides": [
                       {"utc_days": ["friday", "monday"], "utc_start": 2230, "utc_end": 400, "prompt": "0.000008"},
                       {"min_prompt_tokens": 200000, "prompt": "0.000002", "input_cache_write_1h": "0.000004"},
                       {"utc_days": ["saturday", "sunday"], "completion": "1e-999"},
                   ]}
        result = self.read("openrouter", {"data": [{"id": "fixture/conditional-prices", "pricing": pricing}]})
        self.assertEqual(result["models"][0]["pricing"], pricing)
        self.assertEqual(result["models"][0]["pricing"]["overrides"][0]["prompt"], "0.000008")
        self.assertNotIn("pricing_observations", result["models"][0])

    def test_openrouter_observed_minus_one_is_unknown_not_a_charge_or_zero(self) -> None:
        result = self.read("openrouter", {"data": [{"id": "fixture/unresolved-price", "pricing": {
            "prompt": "-1", "completion": "0", "overrides": [{"min_prompt_tokens": 10, "completion": "-1"}]}}]})
        model = result["models"][0]
        self.assertEqual(model["pricing"], {"prompt": None, "completion": "0",
                                            "overrides": [{"min_prompt_tokens": 10, "completion": None}]})
        self.assertEqual(model["pricing_observations"], [
            {"field": "prompt", "raw": "-1", "state": "unknown"},
            {"field": "completion", "raw": "-1", "state": "unknown", "override_index": 0}])
        self.assertEqual(result["model_count"], 1)
        with self.assertRaises(WeaverError):
            self.read("openai", {"data": [{"id": "fixture-model", "pricing": {"prompt": "-1"}}]})

    def test_openrouter_prices_reject_malformed_numbers_types_and_fields(self) -> None:
        for amount in (True, False, 1, 1.5, [], {}, "-2", "-1.0", "-0.01", "NaN", "Infinity", "-Infinity",
                       "1e99999999999999999999999999999999", " 0.1", "", "0\n"):
            with self.subTest(amount=amount), self.assertRaises(WeaverError):
                self.read("openrouter", {"data": [{"id": "fixture/invalid-price", "pricing": {"prompt": amount}}]})
        for pricing in ({"unknown_field": "1"}, {"overrides": {}}, {"overrides": [True]},
                        {"overrides": [{"overrides": []}]}, {"overrides": [{"unexpected": "1"}]},
                        {"discount": True}, {"discount": "0.1"}, {"discount": -0.1}, {"discount": 1.1}):
            with self.subTest(pricing=pricing), self.assertRaises(WeaverError):
                self.read("openrouter", {"data": [{"id": "fixture/invalid-schema", "pricing": pricing}]})

    def test_openrouter_price_conditions_are_typed_without_selecting_a_rate(self) -> None:
        conditions = [
            {"min_prompt_tokens": True}, {"min_prompt_tokens": "10"}, {"min_prompt_tokens": -1},
            {"min_prompt_tokens": 1.5}, {"utc_start": 100}, {"utc_end": 200},
            {"utc_start": 100, "utc_end": 100}, {"utc_start": 100, "utc_end": 2400},
            {"utc_start": 160, "utc_end": 200}, {"utc_start": True, "utc_end": 200},
            {"utc_start": "100", "utc_end": 200}, {"utc_start": -1, "utc_end": 200},
            {"utc_days": []}, {"utc_days": "monday"}, {"utc_days": ["monday", "monday"]},
            {"utc_days": ["Monday"]}, {"utc_days": [None]},
        ]
        for condition in conditions:
            with self.subTest(condition=condition), self.assertRaises(WeaverError):
                self.read("openrouter", {"data": [{"id": "fixture/invalid-condition", "pricing": {
                    "prompt": "1", "overrides": [{**condition, "prompt": "2"}]}}]})

    def test_openrouter_retains_architecture_and_raw_declared_reasoning_metadata(self) -> None:
        architecture = {"input_modalities": ["text", "image"], "output_modalities": ["text", "audio"],
                        "modality": "text+image->text+audio", "tokenizer": "Other", "instruct_type": None}
        reasoning = {"mandatory": False, "default_enabled": True, "supported_efforts": ["high", "low", "none"],
                     "default_effort": "high", "supports_max_tokens": True}
        snapshot = self.read("openrouter", {"data": [{"id": "fixture/multimodal", "architecture": architecture,
                                                      "reasoning": reasoning}]})
        model = snapshot["models"][0]
        self.assertEqual(model["architecture"], architecture)
        self.assertEqual(model["reasoning_metadata"], reasoning)
        self.assertEqual(model["reasoning"]["efforts"], ["high", "low", "none"])
        checked = check_model(ROOT, "openrouter", "fixture/multimodal", snapshot)
        self.assertEqual(checked["architecture"], architecture)
        self.assertEqual(checked["language_agent_compatibility"], "unknown")
        self.assertFalse(checked["inference_tested"])
        for architecture in ({"input_modalities": "text"}, {"output_modalities": ["text", "text"]},
                             {"output_modalities": [False]}, {"tokenizer": []}, {"unvalidated": "value"}):
            with self.subTest(architecture=architecture), self.assertRaises(WeaverError):
                self.read("openrouter", {"data": [{"id": "fixture/invalid-modalities", "architecture": architecture}]})

    def test_openrouter_catalog_keeps_alias_voice_and_top_provider_metadata(self) -> None:
        alias_id = "~fixture/model-latest"
        target_id = "fixture/model-20261114"
        metadata = {
            "canonical_slug": alias_id,
            "alias_target": {"slug": target_id, "name": "Fixture Model"},
            "supported_voices": ["voice_1", "voice-2"],
            "top_provider": {"context_length": None, "max_completion_tokens": 1000, "is_moderated": False},
        }
        snapshot = self.read("openrouter", {"data": [
            {"id": alias_id, "context_length": 4096, **metadata},
            {"id": target_id, "supported_voices": None, "reasoning": {"supported_efforts": ["high"]}},
            {"id": "fixture/legacy"},
        ]})
        alias, target, legacy = snapshot["models"]
        for key, value in metadata.items():
            self.assertEqual(alias[key], value)
        self.assertIsNone(target["supported_voices"])
        self.assertFalse(metadata.keys() & legacy.keys())
        self.assertNotIn("output_limit", alias)
        selected = check_model(ROOT, "openrouter", alias_id, snapshot)
        self.assertEqual(selected["model_id"], alias_id)
        self.assertEqual(selected["alias_target"]["slug"], target_id)
        self.assertEqual(selected["supported_voices"], metadata["supported_voices"])
        self.assertEqual(selected["context_size"], 4096)
        with self.assertRaisesRegex(WeaverError, "effort is unsupported or unknown"):
            check_model(ROOT, "openrouter", alias_id, snapshot, effort="high")

    def test_openrouter_catalog_rejects_invalid_selection_metadata(self) -> None:
        for key, value in (
            ("canonical_slug", "https://example.com/model"),
            ("alias_target", {"slug": "fixture/model"}),
            ("alias_target", {"slug": "../model", "name": "Fixture"}),
            ("supported_voices", "voice"),
            ("supported_voices", ["voice", "voice"]),
            ("top_provider", {"max_completion_tokens": True}),
            ("top_provider", {"context_length": -1}),
            ("top_provider", {"is_moderated": "false"}),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(WeaverError):
                self.read("openrouter", {"data": [{"id": "fixture/model", key: value}]})

    def test_all_supported_provider_formats_import_without_truncation(self) -> None:
        cases = {
            "openrouter": {"data": [{"id": f"publisher/model-{number}"} for number in range(135)]},
            "openai": {"data": [{"id": f"model-{number}"} for number in range(135)]},
            "anthropic": {"data": [{"id": f"model-{number}"} for number in range(135)], "has_more": False},
            "google": {"models": [{"name": f"models/model-{number}"} for number in range(135)]},
            "lm-studio": {"models": [{"key": f"publisher/model-{number}"} for number in range(135)]},
            "perplexity-agent": {"data": [{"id": f"publisher/model-{number}"} for number in range(135)]},
            "perplexity-orchestrator": {"data": [{"id": f"publisher/model-{number}"} for number in range(135)]},
        }
        for provider, response in cases.items():
            with self.subTest(provider=provider):
                snapshot = self.read(provider, response)
                self.assertEqual(snapshot["model_count"], 135)
                self.assertEqual(len(snapshot["models"]), 135)
                self.assertEqual(snapshot["completeness"], "unknown")
                self.assertFalse(snapshot["inference_tested"])

    def test_paginated_snapshots_keep_pages_provenance_and_unknown_prices(self) -> None:
        snapshot = self.read("anthropic", {"provider_id": "anthropic", "pages": [
            {"data": [{"id": "alpha"}], "has_more": True, "last_id": "alpha"},
            {"data": [{"id": "beta"}], "has_more": False, "last_id": "beta"}],
            "provenance": {"source_url": "https://api.anthropic.com/v1/models", "captured_on": "2026-10-26",
                           "scope": "workspace", "auth_scope": "authenticated"}})
        self.assertEqual(snapshot["pagination"]["page_count"], 2)
        self.assertEqual(snapshot["completeness"], "partial")
        self.assertEqual(snapshot["provenance"]["scope"], "workspace")
        self.assertEqual(len(snapshot["provenance"]["sha256"]), 64)
        self.assertNotIn("pricing", snapshot["models"][0])
        self.assertNotIn(str(ROOT), json.dumps(snapshot))

    def test_openrouter_offset_links_and_count_are_retained_without_network(self) -> None:
        snapshot = self.read("openrouter", {"data": [{"id": "vendor/model"}], "total_count": 4,
            "links": {"next": "https://openrouter.ai/api/v1/models?offset=1&limit=1", "prev": None}})
        page = snapshot["pagination"]["pages"][0]
        self.assertEqual(page["total_count"], 4)
        self.assertIn("offset=1", page["links"]["next"])
        self.assertEqual(snapshot["completeness"], "partial")
        with self.assertRaises(WeaverError):
            self.read("openrouter", {"data": [], "links": {"next": "https://openrouter.ai/api/v1/models?api_key=private"}})

    def test_openrouter_pagination_reports_unresolved_captures_with_no_next_link(self) -> None:
        first = {"data": [{"id": "vendor/first"}], "total_count": 2,
                 "links": {"next": "/api/v1/models?offset=1&limit=1"}}
        last = {"data": [{"id": "vendor/last"}], "total_count": 2, "links": {"next": None}}
        scenarios = (
            ([first, last], False),
            ([last], True),
            ([last, first], True),
            ([first, last | {"offset": 2}], True),
            ([first, last | {"total_count": 3}], True),
            ([first | {"links": {"next": "/api/v1/models?offset=2"}}, last], True),
            ([{"data": last["data"], "links": {"previous": "/api/v1/models?offset=0"}}], True),
            ([{"data": first["data"]}, {"data": last["data"]}], True),
            ([{"data": [], "total_count": 0, "links": {"next": None}}], False),
        )
        for pages, unresolved in scenarios:
            with self.subTest(pages=pages):
                result = self.read("openrouter", {"pages": pages})
                self.assertIs(result["pagination"]["has_unresolved_pages"], unresolved)
                self.assertEqual(result["completeness"], "partial" if unresolved else "unknown")

    def test_openrouter_native_reasoning_subsets_and_mandatory_controls(self) -> None:
        snapshot = self.read("openrouter", {"data": [{"id": "vendor/model", "reasoning": {
            "supported_efforts": ["high", "low"], "default_effort": "high", "default_enabled": True,
            "mandatory": True, "supports_max_tokens": True}}]})
        checked = check_model(ROOT, "openrouter", "vendor/model", snapshot, effort="low")
        self.assertEqual(checked["reasoning_payload"], {"reasoning": {"effort": "low"}})
        self.assertTrue(checked["visible_in_snapshot"])
        for options in ({"effort": "medium"}, {"reasoning_enabled": False}, {"effort": "none"}):
            with self.subTest(options=options), self.assertRaises(WeaverError):
                check_model(ROOT, "openrouter", "vendor/model", snapshot, **options)
        budget = check_model(ROOT, "openrouter", "vendor/model", snapshot, budget_tokens=2048)
        self.assertEqual(budget["reasoning_payload"], {"reasoning": {"max_tokens": 2048}})

    def test_openrouter_messages_and_responses_stage_protocol_specific_reasoning(self) -> None:
        snapshot = self.read("openrouter", {"data": [{
            "id": "fixture/model",
            "endpoints": ["/api/v1/messages", "/api/v1/responses"],
            "reasoning": {"supported_efforts": ["high", "none"], "mandatory": False, "supports_max_tokens": True},
        }]})
        spec = get_integration("openrouter", ROOT)
        examples = (
            ("openrouter-messages", {"effort": "high"}, {"output_config": {"effort": "high"}}),
            ("openrouter-messages", {"budget_tokens": 2048}, {"thinking": {"type": "enabled", "budget_tokens": 2048}}),
            ("openrouter-messages", {"reasoning_enabled": True}, {"thinking": {"type": "adaptive"}}),
            ("openrouter-messages", {"reasoning_enabled": False}, {"thinking": {"type": "disabled"}}),
            ("openai-responses", {"effort": "high"}, {"reasoning": {"effort": "high"}}),
            ("openai-responses", {"budget_tokens": 2048}, {"reasoning": {"max_tokens": 2048}}),
            ("openai-responses", {"reasoning_enabled": False}, {"reasoning": {"enabled": False}}),
        )
        for protocol, request, expected in examples:
            with self.subTest(protocol=protocol, request=request):
                artifacts = render_model_setup(spec, "gateway", root=ROOT, provider_id="openrouter",
                                               model_id="fixture/model", snapshot=snapshot, protocol=protocol, **request)
                fragment = next(artifact for artifact in artifacts if artifact.kind == "model-selection")
                self.assertEqual(json.loads(fragment.content), {"model": "fixture/model", **expected})
        for protocol, request in (
            ("openrouter-messages", {"effort": "none"}),
            ("openrouter-messages", {"budget_tokens": 2048, "effort": "high"}),
            ("openai-responses", {"budget_tokens": 2048, "effort": "high"}),
            ("openai-responses", {"mode": "pro"}),
        ):
            with self.subTest(protocol=protocol, request=request), self.assertRaises(WeaverError):
                check_model(ROOT, "openrouter", "fixture/model", snapshot, protocol=protocol, **request)
        for product in ("qwen-code", "kimi-code"):
            with self.subTest(product=product):
                fragments = render_model_setup(
                    get_integration(product, ROOT), "cli", root=ROOT, provider_id="openrouter",
                    model_id="fixture/model", snapshot=snapshot, protocol="openai-responses", reasoning_enabled=False,
                )
                self.assertFalse(any(artifact.kind == "model-config" for artifact in fragments))

    def test_openrouter_endpoint_evidence_is_preserved_and_respected(self) -> None:
        paths = ["/api/v1/chat/completions", "/api/v1/batches", "/api/v2/future-endpoint"]
        snapshot = self.read("openrouter", {"data": [{"id": "fixture/model", "endpoints": paths}]})
        self.assertEqual(snapshot["models"][0]["endpoints"], paths)
        checked = check_model(ROOT, "openrouter", "fixture/model", snapshot)
        self.assertEqual(checked["advertised_endpoints"], paths)
        with self.assertRaisesRegex(WeaverError, "does not advertise"):
            check_model(ROOT, "openrouter", "fixture/model", snapshot, protocol="openai-responses")
        legacy = self.read("openrouter", {"data": [{"id": "fixture/model"}]})
        self.assertIsNone(check_model(ROOT, "openrouter", "fixture/model", legacy)["advertised_endpoints"])
        for invalid in (None, "routes", ["/api/v1/chat/completions?key=fixture"], ["https://example.com/api/v1/images"],
                        ["/api/v1/../images"], ["/api/v1/images", "/api/v1/images"]):
            with self.subTest(invalid=invalid), self.assertRaises(WeaverError):
                self.read("openrouter", {"data": [{"id": "fixture/model", "endpoints": invalid}]})

    def test_openrouter_null_efforts_differs_from_absent_efforts(self) -> None:
        available = self.read("openrouter", {"data": [{"id": "v/a", "reasoning": {"supported_efforts": None}}]})
        self.assertIn("max", available["models"][0]["reasoning"]["efforts"])
        unknown = self.read("openrouter", {"data": [{"id": "v/a", "reasoning": {"mandatory": False}}]})
        with self.assertRaises(WeaverError):
            check_model(ROOT, "openrouter", "v/a", unknown, effort="low")

    def test_lmstudio_native_chat_stages_only_explicit_context_length(self) -> None:
        snapshot = self.read("lm-studio", {"models": [{
            "key": "fixture/local", "type": "llm", "max_context_length": 32768,
            "loaded_instances": [{"id": "local-run", "config": {"context_length": 8192}}],
            "capabilities": {"reasoning": {"allowed_options": ["off", "on"], "default": "on"}},
        }]})
        spec = get_integration("lm-studio", ROOT)
        options = dict(root=ROOT, provider_id="lm-studio", model_id="local-run", snapshot=snapshot,
                       reasoning_enabled=False)
        for model_id, explicit in (("local-run", None), ("local-run", 8192), ("fixture/local", 1024)):
            with self.subTest(model_id=model_id, explicit=explicit):
                artifacts = render_model_setup(spec, "api", **(options | {"model_id": model_id}), context_size=explicit)
                selection = {"model": model_id, "reasoning": "off"}
                if explicit is not None:
                    selection["context_length"] = explicit
                    self.assertIn(b"POST /api/v1/chat", next(item.content for item in artifacts if item.kind == "model-setup"))
                self.assertEqual(json.loads(next(item.content for item in artifacts if item.kind == "model-selection")),
                                 selection)
        with self.assertRaisesRegex(WeaverError, "differs from the captured LM Studio instance"):
            render_model_setup(spec, "api", **options, context_size=1024)
        for protocol in ("openai-chat", "openai-responses", "anthropic-messages"):
            with self.subTest(protocol=protocol), self.assertRaisesRegex(WeaverError, "requires native chat"):
                render_model_setup(spec, "api", **options, protocol=protocol, context_size=1024)

    def test_lmstudio_native_reasoning_is_model_specific(self) -> None:
        snapshot = self.read("lm-studio", {"models": [{"key": "vendor/local-model", "max_context_length": 8192,
            "capabilities": {"reasoning": {"allowed_options": ["off", "on", "high"], "default": "on"}}}]})
        self.assertEqual(check_model(ROOT, "lm-studio", "vendor/local-model", snapshot,
                                     reasoning_enabled=False)["reasoning_payload"], {"reasoning": "off"})
        with self.assertRaises(WeaverError):
            check_model(ROOT, "lm-studio", "vendor/local-model", snapshot, effort="low")

    def test_lmstudio_compatible_reasoning_switches_require_native_model_options(self) -> None:
        model = {"key": "fixture/local", "type": "llm",
                 "capabilities": {"reasoning": {"allowed_options": ["off", "on"], "default": "on"}}}
        snapshot = self.read("lm-studio", {"models": [model]})
        for protocol, payload in (
            ("openai-responses", {"reasoning": {"effort": "none"}}),
            ("openai-chat", {"reasoning_effort": "none"}),
            ("anthropic-messages", {"thinking": {"type": "disabled"}}),
        ):
            with self.subTest(protocol=protocol):
                kwargs = {"protocol": protocol, "reasoning_enabled": False}
                checked = check_model(ROOT, "lm-studio", "fixture/local", snapshot, **kwargs)
                self.assertEqual(checked["reasoning_payload"], payload)
                for extras in ({"effort": "low"}, {"budget_tokens": 128}, {"mode": "adaptive"}):
                    with self.assertRaises(WeaverError):
                        check_model(ROOT, "lm-studio", "fixture/local", snapshot, **kwargs, **extras)
                with self.assertRaises(WeaverError):
                    check_model(ROOT, "lm-studio", "fixture/local", **kwargs)
                with self.assertRaises(WeaverError):
                    check_model(ROOT, "lm-studio", "fixture/local", snapshot, protocol=protocol, effort="high")
        checked = check_model(ROOT, "lm-studio", "fixture/local", snapshot,
                              protocol="anthropic-messages", reasoning_enabled=True)
        self.assertEqual(checked["reasoning_payload"], {"thinking": {"type": "adaptive"}})
        artifacts = render_model_setup(get_integration("codex", ROOT), "cli", root=ROOT,
                                       provider_id="lm-studio", model_id="fixture/local", snapshot=snapshot,
                                       protocol="openai-responses", reasoning_enabled=False)
        profile = next(item for item in artifacts if item.kind == "model-config")
        self.assertEqual(tomllib.loads(profile.content.decode())["model_reasoning_effort"], "none")

    def test_lmstudio_retains_variants_instances_and_one_way_reasoning(self) -> None:
        model = {"key": "fixture/local", "type": "llm", "max_context_length": 32768,
                 "variants": ["fixture/local@q4", "fixture/local@q8"],
                 "selected_variant": "fixture/local@q4",
                 "loaded_instances": [{"id": "local-run", "config": {"context_length": 8192, "parallel": 2}}],
                 "capabilities": {"reasoning": {"allowed_options": ["on"], "default": "on"}}}
        snapshot = self.read("lm-studio", {"models": [model, {"key": "fixture/embed", "type": "embedding",
                                                              "loaded_instances": []},
                                                      {"key": "fixture/decision", "type": "decision"},
                                                      {"key": "fixture/old"}]})
        metadata = snapshot["models"][0]["lm_studio"]
        self.assertEqual(metadata, {
            "type": "llm", "variants": ["fixture/local@q4", "fixture/local@q8"],
            "selected_variant": "fixture/local@q4", "native_reasoning_options": ["on"],
            "loaded_instances": [{"id": "local-run", "context_length": 8192}],
        })
        self.assertEqual(snapshot["models"][0]["context_length"], 32768)
        self.assertEqual(snapshot["models"][1]["lm_studio"]["loaded_instances"], [])
        self.assertIsNone(snapshot["models"][2]["lm_studio"]["loaded_instances"])
        checked = check_model(ROOT, "lm-studio", "fixture/local", snapshot, reasoning_enabled=True)
        self.assertEqual(checked["reasoning_payload"], {"reasoning": "on"})
        self.assertEqual(checked["context_size"], 32768)
        self.assertEqual(checked["availability"], "unknown")
        for args in ({"reasoning_enabled": False}, {"effort": "high"}, {"reasoning_enabled": True, "effort": "high"},
                     {"reasoning_enabled": True, "protocol": "openai-responses"}):
            with self.subTest(args=args), self.assertRaises(WeaverError):
                check_model(ROOT, "lm-studio", "fixture/local", snapshot, **args)
        for key in ("fixture/embed", "fixture/decision"):
            with self.assertRaisesRegex(WeaverError, "chat LLM"):
                check_model(ROOT, "lm-studio", key, snapshot)
        self.assertTrue(check_model(ROOT, "lm-studio", "fixture/old", snapshot)["visible_in_snapshot"])

    def test_lmstudio_chat_alias_uses_parent_reasoning_and_captured_loaded_context(self) -> None:
        row = {"key": "fixture/local", "type": "llm", "max_context_length": 32768,
               "loaded_instances": [{"id": "local-run", "config": {"context_length": 8192}}],
               "capabilities": {"reasoning": {"allowed_options": ["off", "on"], "default": "on"}}}
        snapshot = self.read("lm-studio", {"models": [row]})
        for protocol, payload in [
            ("lmstudio-chat", {"reasoning": "off"}),
            ("openai-responses", {"reasoning": {"effort": "none"}}),
            ("openai-chat", {"reasoning_effort": "none"}),
            ("anthropic-messages", {"thinking": {"type": "disabled"}}),
        ]:
            with self.subTest(protocol=protocol):
                checked = check_model(ROOT, "lm-studio", "local-run", snapshot, protocol=protocol, reasoning_enabled=False)
                self.assertEqual(checked["reasoning_payload"], payload)
                self.assertEqual(checked["model_id"], "local-run")
                self.assertEqual(checked["context_size"], 8192)
                self.assertEqual(checked["lm_studio_loaded_instance"], {"model_key": "fixture/local", "context_length": 8192})
                self.assertTrue(checked["visible_in_snapshot"])
                self.assertEqual(checked["availability"], "unknown")
        self.assertEqual(check_model(ROOT, "lm-studio", "local-run", snapshot, context_size=4096)["context_size"], 4096)
        no_context = self.read("lm-studio", {"models": [row | {"loaded_instances": [{"id": "local-run", "config": {}}]}]})
        self.assertIsNone(check_model(ROOT, "lm-studio", "local-run", no_context)["context_size"])
        artifacts = render_model_setup(get_integration("lm-studio", ROOT), "api", root=ROOT,
                                       provider_id="lm-studio", model_id="local-run", snapshot=snapshot,
                                       reasoning_enabled=True)
        self.assertEqual(json.loads(next(item.content for item in artifacts if item.kind == "model-selection")),
                         {"model": "local-run", "reasoning": "on"})
        self.assertIn(b"loaded instance of `fixture/local`", next(item.content for item in artifacts if item.kind == "model-setup"))
        with self.assertRaises(WeaverError):
            check_model(ROOT, "lm-studio", "local-run", snapshot, effort="high")
        without_instance = self.read("lm-studio", {"models": [row | {"loaded_instances": []}]})
        with self.assertRaises(WeaverError):
            check_model(ROOT, "lm-studio", "local-run", without_instance, reasoning_enabled=False)
        for other in ({"key": "local-run"}, row | {"key": "fixture/other"}):
            collision = self.read("lm-studio", {"models": [row, other]})
            with self.assertRaisesRegex(WeaverError, "Ambiguous LM Studio"):
                check_model(ROOT, "lm-studio", "local-run", collision)
        embedding = self.read("lm-studio", {"models": [row | {"type": "embedding", "capabilities": {}}]})
        with self.assertRaisesRegex(WeaverError, "chat LLM"):
            check_model(ROOT, "lm-studio", "local-run", embedding)

    def test_lmstudio_nonchat_staging_requires_matching_type_and_decision_instance(self) -> None:
        snapshot = self.read("lm-studio", {"models": [
            {"key": "fixture/embed", "type": "embedding", "loaded_instances": []},
            {"key": "fixture/classifier", "type": "decision", "decision_type": "lfm3-d1",
             "has_vision_adapter": False, "loaded_instances": [
                {"id": "decision-run", "config": {"context_length": 2048}},
            ]},
            {"key": "fixture/chat", "type": "llm", "loaded_instances": [
                {"id": "chat-run", "config": {}},
            ]},
        ]})
        decision = snapshot["models"][1]["lm_studio"]
        self.assertEqual(decision["decision_type"], "lfm3-d1")
        self.assertIs(decision["has_vision_adapter"], False)
        self.assertNotIn("has_vision_adapter", snapshot["models"][0]["lm_studio"])
        spec = get_integration("lm-studio", ROOT)
        cases = [
            ("openai-embeddings", "fixture/embed", "embeddings"),
            ("openai-decisions", "decision-run", "decisions"),
            ("jev-systemone", "decision-run", "systemone"),
        ]
        for protocol, identifier, endpoint in cases:
            with self.subTest(protocol=protocol):
                artifacts = render_model_setup(spec, "api", root=ROOT, provider_id="lm-studio",
                                               model_id=identifier, snapshot=snapshot, protocol=protocol)
                self.assertIn(f"POST http://localhost:1234/v1/{endpoint}".encode(), artifacts[0].content)
                self.assertEqual(json.loads(artifacts[1].content), {"model": identifier})
                with self.assertRaises(WeaverError):
                    check_model(ROOT, "lm-studio", identifier, protocol=protocol)
                with self.assertRaises(WeaverError):
                    check_model(ROOT, "lm-studio", identifier, snapshot, protocol=protocol, effort="high")
                with self.assertRaises(WeaverError):
                    check_model(ROOT, "lm-studio", "chat-run", snapshot, protocol=protocol)
        with self.assertRaises(WeaverError):
            check_model(ROOT, "lm-studio", "fixture/classifier", snapshot, protocol="openai-decisions")
        with self.assertRaises(WeaverError):
            render_model_setup(spec, "desktop", root=ROOT, provider_id="lm-studio", model_id="decision-run",
                               snapshot=snapshot, protocol="jev-systemone")

    def test_lmstudio_rejects_inconsistent_variant_and_instance_metadata(self) -> None:
        for fields in ({"variants": ["fixture/local@q4"], "selected_variant": "fixture/local@q8"},
                       {"loaded_instances": [{"id": "run", "config": {"context_length": True}}]},
                       {"loaded_instances": [{"id": "run", "config": {}}, {"id": "run", "config": {}}]}):
            with self.subTest(fields=fields), self.assertRaises(WeaverError):
                self.read("lm-studio", {"models": [{"key": "fixture/local", **fields}]})

    def test_lmstudio_rejects_malformed_decision_metadata(self) -> None:
        for fields in ({"decision_type": ""}, {"has_vision_adapter": 0}):
            with self.subTest(fields=fields), self.assertRaises(WeaverError):
                self.read("lm-studio", {"models": [{"key": "fixture/decision", "type": "decision", **fields}]})

    def test_input_limit_is_not_promoted_to_total_context_window(self) -> None:
        snapshot = self.read("anthropic", {"data": [{"id": "custom-claude", "max_input_tokens": 64000}]})
        self.assertEqual(snapshot["models"][0]["input_limit"], 64000)
        self.assertNotIn("context_length", snapshot["models"][0])
        checked = check_model(ROOT, "anthropic", "custom-claude", snapshot)
        self.assertIsNone(checked["context_size"])

    def test_anthropic_capabilities_describe_effort_subset(self) -> None:
        snapshot = self.read("anthropic", {"data": [{"id": "custom-claude", "capabilities": {
            "effort": {"low": {"supported": True}, "high": {"supported": False}},
            "thinking": {"types": {"adaptive": {"supported": True}, "disabled": {"supported": True}}}}}]})
        checked = check_model(ROOT, "anthropic", "custom-claude", snapshot, effort="low", reasoning_enabled=True)
        self.assertEqual(checked["reasoning_payload"], {"output_config": {"effort": "low"},
                                                       "thinking": {"type": "adaptive"}})
        with self.assertRaises(WeaverError):
            check_model(ROOT, "anthropic", "custom-claude", snapshot, effort="high")

    def test_malformed_duplicate_secret_and_infinite_values_rejected(self) -> None:
        invalid = [
            {"data": [{"id": "valid"}, {"id": "valid"}]}, {"data": ["valid"]}, {"data": [{"id": "../bad"}]},
            {"data": [{"id": "valid", "api_key": "redacted"}]}, {"data": [{"id": "valid", "x": float("inf")}]},
            {"data": [{"id": "valid", "pricing": {"prompt": "NaN"}}]},
            {"data": [{"id": "valid", "context_length": True}]},
            {"data": [{"id": "valid", "unused": "sk-proj-example-secret-value"}]},
            {"data": [{"id": "valid"}], "has_more": "false"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(WeaverError):
                self.read("openai", payload)

    def test_duplicate_json_keys_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text('{"data": [], "data": []}')
            with self.assertRaises(WeaverError):
                read_model_snapshot(path, "openai", ROOT)

    def test_provider_zero_context_is_preserved_without_inventing_a_usable_limit(self) -> None:
        snapshot = self.read("openrouter", {"data": [{"id": "fixture/zero-context", "context_length": 0}]})
        self.assertEqual(snapshot["models"][0]["context_length"], 0)
        checked = check_model(ROOT, "openrouter", "fixture/zero-context", snapshot)
        self.assertTrue(checked["visible_in_snapshot"])
        self.assertIsNone(checked["context_size"])
        self.assertEqual(check_model(ROOT, "openrouter", "fixture/zero-context", snapshot,
                                     context_size=8192)["context_size"], 8192)
        self.assertEqual(snapshot["models"][0]["context_length"], 0)


class ModelValidationTests(unittest.TestCase):
    def test_context_overrides_do_not_hide_invalid_snapshot_limits(self) -> None:
        snapshot = {"provider_id": "openrouter", "models": [{"id": "fixture/context-limit", "context_length": 0}]}
        for context in (0, -1, True, "8192", 8192.0):
            with self.subTest(context=context), self.assertRaises(WeaverError):
                check_model(ROOT, "openrouter", "fixture/context-limit", snapshot, context_size=context)
        for context in (-1, True, "0", 0.0, None, {}):
            snapshot["models"][0]["context_length"] = context
            for override in (None, 8192):
                with self.subTest(context=context, override=override), self.assertRaises(WeaverError):
                    check_model(ROOT, "openrouter", "fixture/context-limit", snapshot, context_size=override)

    def test_explicit_id_is_not_visibility_or_success(self) -> None:
        checked = check_model(ROOT, "openai", "unknown-future-model")
        self.assertTrue(checked["accepted_explicit_id"])
        self.assertIsNone(checked["visible_in_snapshot"])
        self.assertEqual(checked["availability"], "unknown")
        self.assertFalse(checked["inference_tested"])
        for options in ({"effort": "high"}, {"budget_tokens": 1024}, {"reasoning_enabled": True}, {"mode": "pro"}):
            with self.subTest(options=options), self.assertRaises(WeaverError):
                check_model(ROOT, "openai", "unknown-future-model", **options)

    def test_openai_mode_is_independent_and_protocol_specific(self) -> None:
        checked = check_model(ROOT, "openai", "gpt-6-sol", effort="low", mode="pro")
        self.assertEqual(checked["reasoning_payload"], {"reasoning": {"effort": "low", "mode": "pro"}})
        for options in ({"protocol": "openai-chat", "mode": "pro"}, {"effort": "minimal"}, {"mode": "adaptive"}):
            with self.subTest(options=options), self.assertRaises(WeaverError):
                check_model(ROOT, "openai", "gpt-6-sol", **options)
        self.assertEqual(check_model(ROOT, "openai", "gpt-6-sol", protocol="openai-chat", effort="high")["reasoning_payload"],
                         {"reasoning_effort": "high"})

    def test_budgets_toggles_and_google_levels_have_distinct_shapes(self) -> None:
        google = check_model(ROOT, "google", "models/gemini-2.5-flash", budget_tokens=2048)
        self.assertEqual(google["reasoning_payload"], {"generationConfig": {"thinkingConfig": {"thinkingBudget": 2048}}})
        level = check_model(ROOT, "google", "models/gemini-3.8-flash", effort="low")
        self.assertEqual(level["reasoning_payload"], {"generationConfig": {"thinkingConfig": {"thinkingLevel": "low"}}})
        disabled = check_model(ROOT, "google", "models/gemini-2.5-flash", reasoning_enabled=False)
        self.assertEqual(disabled["reasoning_payload"]["generationConfig"]["thinkingConfig"]["thinkingBudget"], 0)
        claude = check_model(ROOT, "anthropic", "claude-sonnet-4-6", budget_tokens=1024)
        self.assertEqual(claude["reasoning_payload"], {"thinking": {"type": "enabled", "budget_tokens": 1024}})
        for amount in (True, -1, 0, 1023):
            with self.subTest(amount=amount), self.assertRaises(WeaverError):
                check_model(ROOT, "anthropic", "claude-sonnet-4-6", budget_tokens=amount)

    def test_unsafe_ids_and_invalid_protocols_fail_without_echoing_secret(self) -> None:
        for value in ("../x", "a/../b", "C:/Users/private", "x?key=value", "a;pwd", "a\nbody", "sk-proj-private-secret", "a\\b"):
            with self.subTest(value=value), self.assertRaises(WeaverError) as error:
                check_model(ROOT, "openai", value)
            self.assertNotIn(value, str(error.exception))
        with self.assertRaises(WeaverError):
            check_model(ROOT, "openai", "safe-model", protocol="anthropic-messages")


class ModelRenderingTests(unittest.TestCase):
    def test_perplexity_agent_fallback_checks_each_model_and_stages_an_ordered_request(self) -> None:
        spec = get_integration("perplexity", ROOT)
        primary, fallback, other = "perplexity/kimi-k3", "openai/fixture", "anthropic/fixture"
        snapshot = {"provider_id": "perplexity-agent", "models": [
            {"id": primary}, {"id": fallback, "reasoning": {"efforts": ["high"]}}, {"id": other},
        ]}
        options = dict(root=ROOT, provider_id="perplexity-agent", model_id=primary, snapshot=snapshot)
        artifacts = render_model_setup(spec, "agent-api", **options, effort="high", perplexity_fallbacks=[fallback])
        fields, = [item for item in artifacts if item.kind == "model-selection"]
        self.assertEqual(json.loads(fields.content), {
            "models": [primary, fallback], "reasoning": {"effort": "high"},
        })
        self.assertIn(b"POST /v1/agent", artifacts[0].content)
        artifacts = render_model_setup(spec, "agent-api", **options, perplexity_fallbacks=[other, fallback],
                                       perplexity_max_output_tokens=8192)
        fields, = [item for item in artifacts if item.kind == "model-selection"]
        self.assertEqual(json.loads(fields.content), {"models": [primary, other, fallback], "max_output_tokens": 8192})
        for invalid in ([], [primary], [fallback, fallback], ["missing"], [fallback] * 5):
            with self.subTest(invalid=invalid), self.assertRaises(WeaverError):
                render_model_setup(spec, "agent-api", **options, perplexity_fallbacks=invalid)
        with self.assertRaisesRegex(WeaverError, "unsupported or unknown"):
            render_model_setup(spec, "agent-api", **options, effort="max", perplexity_fallbacks=[fallback])
        for invalid_budget in (0, 2**31):
            with self.subTest(invalid_budget=invalid_budget), self.assertRaisesRegex(WeaverError, "positive 32-bit integer"):
                render_model_setup(spec, "agent-api", **options, perplexity_max_output_tokens=invalid_budget)

    def test_perplexity_documented_model_efforts_and_optional_output_budget(self) -> None:
        spec = get_integration("perplexity", ROOT)
        options = dict(root=ROOT, provider_id="perplexity-agent")
        combinations = {
            "openai/gpt-6-luna": ("none", "minimal", "medium"),
            "openai/gpt-5.6-luna": ("none",),
            "openai/gpt-6.1-terra": ("medium",),
            "anthropic/claude-opus-5-5": ("high",),
            "openai/gpt-6.1-sol": ("high",),
        }
        for name, efforts in combinations.items():
            for effort in efforts:
                with self.subTest(name=name, effort=effort):
                    artifacts = render_model_setup(spec, "agent-api", **options, model_id=name, effort=effort)
                    fields, = [item for item in artifacts if item.kind == "model-selection"]
                    self.assertEqual(json.loads(fields.content), {"model": name, "reasoning": {"effort": effort}})
        models = ["anthropic/claude-opus-5-5", "openai/gpt-6.1-sol"]
        artifacts = render_model_setup(spec, "agent-api", **options, model_id=models[0],
                                       perplexity_fallbacks=models[1:], effort="high")
        fields, = [item for item in artifacts if item.kind == "model-selection"]
        self.assertEqual(json.loads(fields.content), {"models": models, "reasoning": {"effort": "high"}})
        snapshot = {"provider_id": "perplexity-agent", "models": [{"id": models[0]}]}
        with self.assertRaisesRegex(WeaverError, "must list every model"):
            render_model_setup(spec, "agent-api", **options, model_id=models[0],
                               perplexity_fallbacks=models[1:], snapshot=snapshot)
        with self.assertRaisesRegex(WeaverError, "requires documented models"):
            render_model_setup(spec, "agent-api", **options, model_id=models[0],
                               perplexity_fallbacks=["unlisted/fixture"])

    def test_perplexity_max_effort_keeps_rest_payload_and_explains_python_sdk_workaround(self) -> None:
        artifacts = render_model_setup(get_integration("perplexity", ROOT), "agent-api", root=ROOT,
                                       provider_id="perplexity-agent", model_id="perplexity/kimi-k3", effort="max")
        fields, = [item for item in artifacts if item.kind == "model-selection"]
        handoff, = [item.content.decode("utf-8") for item in artifacts if item.kind == "model-setup"]
        self.assertEqual(json.loads(fields.content), {"model": "perplexity/kimi-k3", "reasoning": {"effort": "max"}})
        self.assertIn('extra_body={"reasoning": {"effort": "max"}}', handoff)
        self.assertIn("Perplexity Python SDK 0.46.1", handoff)
        self.assertIn("omit its typed `reasoning` argument", handoff)

    def test_perplexity_agent_responses_alias_preserves_provider_controls(self) -> None:
        spec = get_integration("perplexity", ROOT)
        options = dict(root=ROOT, provider_id="perplexity-agent", protocol="openai-responses")
        cases = (
            ({"model_id": "perplexity/kimi-k3", "effort": "max"},
             {"model": "perplexity/kimi-k3", "reasoning": {"effort": "max"}}),
            ({"model_id": "openai/gpt-6-luna", "effort": "minimal"},
             {"model": "openai/gpt-6-luna", "reasoning": {"effort": "minimal"}}),
            ({"model_id": "anthropic/claude-opus-5-5", "effort": "high",
              "perplexity_fallbacks": ["openai/gpt-6.1-sol"], "perplexity_max_steps": 4},
             {"models": ["anthropic/claude-opus-5-5", "openai/gpt-6.1-sol"],
              "reasoning": {"effort": "high"}, "max_steps": 4}),
            ({"model_id": None, "perplexity_effort_preset": "ultra@7"},
             {"effort": "ultra@7"}),
        )
        for args, expected in cases:
            with self.subTest(args=args):
                handoff, selection = render_model_setup(spec, "agent-api", **options, **args)
                self.assertEqual(json.loads(selection.content), expected)
                self.assertIn(b"POST /v1/responses", handoff.content)
                self.assertIn(b"https://api.perplexity.ai/v1", handoff.content)
                self.assertIn(b"`extra_body`", handoff.content)
        with self.assertRaisesRegex(WeaverError, "unsupported or unknown"):
            render_model_setup(spec, "agent-api", **options, model_id="openai/gpt-6-luna", effort="max")

    def test_perplexity_service_tiers_require_route_and_exact_model_coverage(self) -> None:
        spec = get_integration("perplexity", ROOT)
        supported = ("openai/gpt-6.1-sol", "openai/gpt-6-astra", "openai/gpt-6-sol", "openai/gpt-6-luna",
                     "openai/gpt-5.6-sol", "openai/gpt-5.6-terra", "openai/gpt-5.6-luna", "openai/gpt-5.5")
        default_only = "perplexity/kimi-k3"
        unknown = "openai/unlisted"
        options = dict(root=ROOT, provider_id="perplexity-agent",
                       snapshot={"provider_id": "perplexity-agent",
                                 "models": [{"id": model} for model in (*supported, default_only, unknown)]})
        for model in supported:
            with self.subTest(model=model):
                artifacts = render_model_setup(spec, "agent-api", model_id=model,
                                               perplexity_service_tier="flex", **options)
                selection, = [item for item in artifacts if item.kind == "model-selection"]
                self.assertEqual(json.loads(selection.content), {"model": model, "service_tier": "flex"})
        for tier, expected in (("fast", "priority"), ("priority", "priority"), ("default", "default"),
                                ("auto", "auto"), (None, None)):
            with self.subTest(tier=tier):
                artifacts = render_model_setup(spec, "agent-api", model_id=supported[0],
                                               perplexity_fallbacks=[supported[1]],
                                               perplexity_service_tier=tier, **options)
                selection, = [item for item in artifacts if item.kind == "model-selection"]
                payload = {"models": list(supported[:2])}
                if expected is not None:
                    payload["service_tier"] = expected
                self.assertEqual(json.loads(selection.content), payload)
        for model in (default_only, unknown):
            with self.subTest(model=model), self.assertRaisesRegex(WeaverError, "every selected model"):
                render_model_setup(spec, "agent-api", model_id=supported[0], perplexity_fallbacks=[model],
                                   perplexity_service_tier="priority", **options)
            with self.subTest(model=model), self.assertRaisesRegex(WeaverError, "every selected model"):
                render_model_setup(spec, "agent-api", model_id=model, perplexity_service_tier="priority", **options)
        artifacts = render_model_setup(spec, "agent-api", model_id=default_only, effort="max",
                                       perplexity_service_tier="auto", **options)
        selection, = [item for item in artifacts if item.kind == "model-selection"]
        self.assertEqual(json.loads(selection.content), {
            "model": default_only, "reasoning": {"effort": "max"}, "service_tier": "auto",
        })
        with self.assertRaisesRegex(WeaverError, "Unsupported Perplexity Agent API service tier"):
            render_model_setup(spec, "agent-api", model_id=supported[0], perplexity_service_tier="slow", **options)
        with self.assertRaisesRegex(WeaverError, "require the Perplexity Agent API route"):
            render_model_setup(spec, "orchestrator", root=ROOT, provider_id="perplexity-orchestrator",
                               model_id=supported[0], perplexity_service_tier="default")

    def test_zed_openrouter_default_and_subagent_settings_use_raw_model_id(self) -> None:
        spec = get_integration("zed", ROOT)
        options = dict(root=ROOT, provider_id="openrouter", model_id="fixture/reasoner",
                       snapshot={"provider_id": "openrouter", "models": [
                           {"id": "fixture/reasoner", "context_length": 32000,
                            "supported_parameters": ["reasoning"],
                            "reasoning": {"efforts": ["none", "xhigh"], "toggle": True}},
                       ]})
        for subagent, field in ((False, "default_model"), (True, "subagent_model")):
            for requested, expected in (
                ({}, {}),
                ({"effort": "xhigh"}, {"enable_thinking": True, "effort": "xhigh"}),
                ({"effort": "none"}, {"enable_thinking": False, "effort": "none"}),
                ({"reasoning_enabled": False}, {"enable_thinking": False}),
            ):
                with self.subTest(field=field, requested=requested):
                    artifacts = render_model_setup(spec, "ide", zed_subagent=subagent, **options, **requested)
                    artifact = next(item for item in artifacts if item.kind == "model-config")
                    self.assertEqual(artifact.relative_path.as_posix(), f"zed/model/{field}.fragment.json")
                    self.assertEqual(json.loads(artifact.content), {"agent": {field: {
                        "provider": "openrouter", "model": "fixture/reasoner", **expected,
                    }}})
        with self.assertRaises(WeaverError):
            render_model_setup(spec, "ide", zed_subagent=True, effort="high", **options)
        no_reasoning_parameter = options | {"snapshot": {"provider_id": "openrouter", "models": [
            {"id": "fixture/reasoner", "supported_parameters": [],
             "reasoning": {"efforts": ["xhigh"]}},
        ]}}
        with self.assertRaisesRegex(WeaverError, "parameter used by Zed"):
            render_model_setup(spec, "ide", zed_subagent=True, effort="xhigh", **no_reasoning_parameter)
        with self.assertRaisesRegex(WeaverError, "Zed subagent settings"):
            render_model_setup(spec, "ide", zed_subagent=True, context_size=32000, **options)
        with self.assertRaisesRegex(WeaverError, "Zed subagent settings"):
            render_model_setup(get_integration("openrouter", ROOT), "gateway", zed_subagent=True, **options)
        non_text = options | {"snapshot": {"provider_id": "openrouter", "models": [
            {"id": "fixture/reasoner", "architecture": {"output_modalities": ["image"]}},
        ]}}
        self.assertFalse(any(item.kind == "model-config" for item in render_model_setup(spec, "ide", **non_text)))
        with self.assertRaisesRegex(WeaverError, "Zed subagent settings"):
            render_model_setup(spec, "ide", zed_subagent=True, **non_text)

    def test_zed_subagent_settings_are_staged_via_cli(self) -> None:
        from tests.test_cli_exports import invoke

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            code, _, error = invoke("model", "--root", str(ROOT), "--agent", "zed", "--surface", "ide",
                                    "--provider", "openrouter", "--model", "openrouter/auto",
                                    "--zed-subagent", "--output-dir", directory)
            self.assertEqual((code, error), (0, ""))
            fragment = output / "zed/model/subagent_model.fragment.json"
            self.assertEqual(json.loads(fragment.read_text(encoding="utf-8")), {
                "agent": {"subagent_model": {"provider": "openrouter", "model": "openrouter/auto"}},
            })

    def test_zed_profile_stages_only_the_named_profile_and_validates_selection(self) -> None:
        spec = get_integration("zed", ROOT)
        options = dict(root=ROOT, provider_id="openrouter", model_id="fixture/reasoner",
                       snapshot={"provider_id": "openrouter", "models": [
                           {"id": "fixture/reasoner", "supported_parameters": ["reasoning"],
                            "reasoning": {"efforts": ["none", "high"], "toggle": True}},
                       ]})
        for effort, thinking in ((None, {}), ("none", {"effort": "none", "enable_thinking": False}),
                                 ("high", {"effort": "high", "enable_thinking": True})):
            with self.subTest(effort=effort):
                rendered = render_model_setup(spec, "ide", **options, effort=effort, zed_profile="weaver-review")
                fragment, = (item for item in rendered if item.kind == "model-config")
                self.assertEqual(fragment.relative_path.as_posix(), "zed/model/agent-profile.fragment.json")
                self.assertEqual(json.loads(fragment.content), {"agent": {"profiles": {"weaver-review": {
                    "name": "weaver-review",
                    "tools": {"find_path": True, "grep": True, "list_directory": True, "read_file": True},
                    "enable_all_context_servers": False,
                    "context_servers": {},
                    "default_model": {"provider": "openrouter", "model": "fixture/reasoner", **thinking},
                }}}})
        for profile_id in ("", "ask", "minimal", "write", "../review", "A", "a" * 65):
            with self.subTest(profile=profile_id), self.assertRaisesRegex(WeaverError, "Zed profile ID"):
                render_model_setup(spec, "ide", **options, zed_profile=profile_id)
        with self.assertRaisesRegex(WeaverError, "Zed profile settings"):
            render_model_setup(spec, "ide", **options, zed_profile="weaver-review", zed_subagent=True)

    def test_zed_named_profile_is_explicit_and_target_bound_in_cli(self) -> None:
        from tests.test_cli_exports import invoke

        args = ("model", "--root", str(ROOT), "--provider", "openrouter", "--model", "openrouter/auto",
                "--zed-profile", "weaver-review", "--dry-run")
        code, output, error = invoke(*args, "--agent", "zed", "--surface", "ide")
        self.assertEqual((code, error), (0, ""))
        fragment, = [item for item in json.loads(output)["artifacts"] if item["kind"] == "model-config"]
        self.assertIn("weaver-review", json.loads(fragment["content"])["agent"]["profiles"])
        code, _, error = invoke(*args, "--agent", "openrouter", "--surface", "gateway")
        self.assertEqual(code, 2)
        self.assertIn("require a supported native Zed IDE model", error)

    def test_zed_lmstudio_uses_native_model_ids_from_v0_catalog(self) -> None:
        provider = "lm-studio"
        spec = get_integration("zed", ROOT)
        raw = {"object": "list", "data": [
            {"id": "fixture/vision", "object": "model", "type": "vlm", "max_context_length": 8192},
            {"id": "fixture/language", "object": "model", "type": "llm", "max_context_length": 4096},
            {"id": "fixture/embedding", "object": "model", "type": "embeddings", "max_context_length": 2048},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lmstudio-models-v0.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            snapshot = read_model_snapshot(path, provider, ROOT)
            raw["data"][0].pop("type")
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(WeaverError, "typed LM Studio v0 model"):
                read_model_snapshot(path, provider, ROOT)
        self.assertIn("LM Studio v0 model-list shape", snapshot["provenance"]["discovery_scope"])
        self.assertEqual(snapshot["completeness"], "unknown")
        self.assertEqual(snapshot["models"][0]["context_length"], 8192)
        self.assertEqual(snapshot["models"][0]["reasoning"]["state"], "unknown")
        expected = {"provider": "lmstudio", "model": "fixture/vision"}
        for field, options in (("default_model", {}), ("subagent_model", {"zed_subagent": True}),
                               ("profiles", {"zed_profile": "weaver-local"})):
            with self.subTest(field=field):
                rendered = render_model_setup(spec, "ide", root=ROOT, provider_id=provider,
                                              model_id="fixture/vision", snapshot=snapshot, **options)
                artifact, = [item for item in rendered if item.kind == "model-config"]
                settings = json.loads(artifact.content)["agent"]
                if field == "profiles":
                    self.assertEqual(settings[field]["weaver-local"]["default_model"], expected)
                else:
                    self.assertEqual(settings, {field: expected})
                self.assertIn(b"Provider API base: `http://localhost:1234/api/v0`", rendered[0].content)
                self.assertIn(b"LMSTUDIO_API_KEY", rendered[0].content)
                self.assertEqual(json.loads(rendered[-1].content), {"model": "fixture/vision"})
        with self.assertRaisesRegex(WeaverError, "not declared as a chat LLM"):
            render_model_setup(spec, "ide", root=ROOT, provider_id=provider,
                               model_id="fixture/embedding", snapshot=snapshot)
        with self.assertRaisesRegex(WeaverError, "does not list"):
            render_model_setup(spec, "ide", root=ROOT, provider_id=provider,
                               model_id="fixture/missing", snapshot=snapshot)

    def test_zed_lmstudio_preserves_other_protocol_handoffs_and_rejects_native_tuning(self) -> None:
        spec = get_integration("zed", ROOT)
        options = dict(root=ROOT, provider_id="lm-studio", model_id="fixture/llm")
        for requested in ({"effort": "high"}, {"reasoning_enabled": False},
                          {"budget_tokens": 1024}, {"mode": "pro"}, {"context_size": 4096}):
            with self.subTest(requested=requested), self.assertRaisesRegex(WeaverError, "built-in LM Studio provider"):
                render_model_setup(spec, "ide", **options, **requested)
        other = render_model_setup(spec, "ide", **options, protocol="openai-responses")
        self.assertFalse(any(item.kind == "model-config" for item in other))
        self.assertIn(b"http://localhost:1234/v1", other[0].content)

    def test_zed_native_openai_stages_built_in_responses_reasoning(self) -> None:
        spec = get_integration("zed", ROOT)
        cases = [
            ("gpt-5", {"effort": "minimal"}, {"enable_thinking": True, "effort": "minimal"}),
            ("gpt-6.1-sol", {"effort": "max"}, {"enable_thinking": True, "effort": "max"}),
            ("gpt-6-luna", {"effort": "none"}, {"enable_thinking": False}),
            ("gpt-5.5", {"reasoning_enabled": False}, {"enable_thinking": False}),
            ("gpt-6-sol", {"reasoning_enabled": True}, {"enable_thinking": True}),
            ("configured-alias", {}, {}),
        ]
        for model, options, fields in cases:
            with self.subTest(model=model, options=options):
                artifacts = render_model_setup(spec, "ide", root=ROOT, provider_id="openai", model_id=model,
                                               zed_subagent=True, **options)
                fragment, = [item for item in artifacts if item.kind == "model-config"]
                self.assertEqual(json.loads(fragment.content),
                                 {"agent": {"subagent_model": {"provider": "openai", "model": model, **fields}}})
        for model, options in (("gpt-6.1-sol", {"reasoning_enabled": False}),
                               ("gpt-5.5", {"effort": "max"}), ("gpt-4", {"effort": "low"}),
                               ("configured-alias", {"effort": "high"})):
            with self.subTest(model=model), self.assertRaisesRegex(WeaverError, "built-in OpenAI model"):
                render_model_setup(spec, "ide", root=ROOT, provider_id="openai", model_id=model, **options)

    def test_zed_native_anthropic_reasoning_uses_adaptive_model_capabilities(self) -> None:
        from tests.test_cli_exports import invoke

        spec = get_integration("zed", ROOT)
        supported = {"supported": True}
        model = "claude-opus-5"
        row = {"id": model, "capabilities": {
            "effort": {"high": supported, "xhigh": supported},
            "thinking": {"supported": True, "types": {"adaptive": supported, "disabled": supported}},
        }}
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "claude.json"
            catalog.write_text(json.dumps({"data": [row]}), encoding="utf-8")
            code, output, error = invoke("model", "--root", str(ROOT), "--agent", "zed", "--surface", "ide",
                                         "--provider", "anthropic", "--model", model, "--catalog", str(catalog),
                                         "--reasoning-effort", "xhigh", "--zed-profile", "claude-review", "--dry-run")
            self.assertEqual((code, error), (0, ""))
            fragment, = [item for item in json.loads(output)["artifacts"] if item["kind"] == "model-config"]
            self.assertEqual(json.loads(fragment["content"])["agent"]["profiles"]["claude-review"]["default_model"], {
                "provider": "anthropic", "model": model, "enable_thinking": True, "effort": "xhigh",
            })
            no_adaptive = row | {"capabilities": {"effort": {"high": supported}}}
            catalog.write_text(json.dumps({"data": [no_adaptive]}), encoding="utf-8")
            with self.assertRaisesRegex(WeaverError, "requires adaptive thinking"):
                render_model_setup(spec, "ide", root=ROOT, provider_id="anthropic", model_id=model, effort="high",
                                   snapshot=read_model_snapshot(catalog, "anthropic", ROOT))
        with self.assertRaisesRegex(WeaverError, "saved Anthropic catalog"):
            render_model_setup(spec, "ide", root=ROOT, provider_id="anthropic", model_id=model, effort="high")

    def test_zed_speed_stages_settings_and_provider_request_fields(self) -> None:
        from tests.test_cli_exports import invoke

        base = ("model", "--root", str(ROOT), "--agent", "zed", "--surface", "ide", "--dry-run")
        cases = (
            ("openai", "gpt-6.1-sol", "fast", ("--zed-subagent", "--reasoning-effort", "max"),
             {"service_tier": "priority", "reasoning": {"effort": "max"}}),
            ("openai", "gpt-5.5", "standard", (), {}),
            ("anthropic", "claude-opus-5", "fast", ("--zed-profile", "opus-review"), {"speed": "fast"}),
            ("anthropic", "claude-opus-4-8", "standard", (), {"speed": "standard"}),
        )
        for provider, model, speed, options, wire in cases:
            with self.subTest(provider=provider, model=model, speed=speed):
                code, output, error = invoke(*base, "--provider", provider, "--model", model, "--zed-speed", speed, *options)
                self.assertEqual((code, error), (0, ""))
                fragments = {item["kind"]: json.loads(item["content"]) for item in json.loads(output)["artifacts"]
                             if item["kind"] in {"model-config", "model-selection"}}
                config = fragments["model-config"]["agent"]
                if "--zed-profile" in options:
                    config = config["profiles"]["opus-review"]
                selected = config["subagent_model" if "--zed-subagent" in options else "default_model"]
                self.assertEqual(selected["speed"], speed)
                self.assertEqual(fragments["model-selection"], {"model": model, **wire})
        for provider, model in (("openai", "gpt-5.5-pro"), ("anthropic", "claude-sonnet-4-6"),
                                ("openrouter", "openrouter/auto"), ("google", "gemini-3.8-flash")):
            with self.subTest(provider=provider, model=model):
                code, _, _ = invoke(*base, "--provider", provider, "--model", model, "--zed-speed", "fast")
                self.assertEqual(code, 2)
        with self.assertRaisesRegex(WeaverError, "catalog does not list"):
            render_model_setup(get_integration("zed", ROOT), "ide", root=ROOT, provider_id="openai",
                               model_id="gpt-6.1-sol", zed_speed="fast",
                               snapshot={"provider_id": "openai", "models": [{"id": "gpt-5.5"}]})

    def test_zed_gemini_thinking_uses_versioned_catalog_ids_and_zed_levels(self) -> None:
        from tests.test_cli_exports import invoke

        def row(model, **fields):
            return {"name": "models/" + model, "thinking": True, "inputTokenLimit": 1048576,
                    "outputTokenLimit": 65536, "supportedGenerationMethods": ["generateContent"], **fields}

        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "gemini.json"
            models = ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro",
                      "gemini-2.5-flash-lite", "gemini-4-hydrogen", "gemini-3.8-flash-latest")
            catalog.write_text(json.dumps({"models": [row(model) for model in models] +
                                           [row("gemini-3-no-content") | {"supportedGenerationMethods": ["embedContent"]}]}),
                               encoding="utf-8")
            args = ("model", "--root", str(ROOT), "--agent", "zed", "--surface", "ide", "--provider", "google",
                    "--catalog", str(catalog), "--dry-run", "--zed-subagent")
            examples = [
                ("models/gemini-3.8-flash", ("--reasoning-effort", "medium"),
                 {"enable_thinking": True, "effort": "medium"}, {"thinkingLevel": "MEDIUM", "includeThoughts": True}),
                ("gemini-3.5-flash", ("--reasoning-effort", "minimal"),
                 {"enable_thinking": True, "effort": "minimal"}, {"thinkingLevel": "MINIMAL", "includeThoughts": True}),
                ("gemini-3.8-flash", ("--reasoning", "off"), {"enable_thinking": False}, {"thinkingLevel": "LOW"}),
                ("gemini-2.5-flash", ("--reasoning", "off"), {"enable_thinking": False}, {"thinkingBudget": 0}),
                ("gemini-2.5-pro", ("--reasoning", "off"), {"enable_thinking": False}, {}),
            ]
            for model, flags, settings, wire in examples:
                with self.subTest(model=model, flags=flags):
                    code, output, errors = invoke(*args, "--model", model, *flags)
                    self.assertEqual((code, errors), (0, ""))
                    fragments = {item["kind"]: json.loads(item["content"]) for item in json.loads(output)["artifacts"]
                                 if item["kind"] in ("model-config", "model-selection")}
                    bare = model.removeprefix("models/")
                    self.assertEqual(fragments["model-config"]["agent"]["subagent_model"],
                                     {"provider": "google", "model": bare, **settings})
                    self.assertEqual(fragments["model-selection"],
                                     {"model": "models/" + bare, **({"generationConfig": {"thinkingConfig": wire}} if wire else {})})
            for model, flags in (
                ("gemini-4-hydrogen", ("--reasoning-effort", "xhigh")),
                ("gemini-3.8-flash", ("--reasoning-effort", "minimal")),
                ("gemini-3.8-flash", ("--reasoning-budget", "1024")),
                ("gemini-2.5-flash", ("--reasoning-effort", "low")),
                ("gemini-2.5-flash-lite", ("--reasoning-budget", "511")),
                ("gemini-2.5-pro", ("--reasoning-budget", "32769")),
                ("gemini-3.8-flash-latest", ()), ("gemini-3-no-content", ()),
            ):
                with self.subTest(model=model, flags=flags):
                    self.assertEqual(invoke(*args, "--model", model, *flags)[0], 2)

    def test_zed_gemini_budget_stages_a_separate_provider_model(self) -> None:
        spec = get_integration("zed", ROOT)
        model = "gemini-2.5-flash-lite"
        snapshot = {"provider_id": "google", "models": [{
            "id": f"models/{model}", "display_name": "Gemini 2.5 Flash-Lite", "google_thinking": False,
            "input_limit": 1048576, "output_limit": 65536, "supported_methods": ["generateContent"],
        }]}
        options = dict(root=ROOT, provider_id="google", model_id=model, snapshot=snapshot,
                       zed_profile="gemini-review", budget_tokens=1024)
        artifacts = render_model_setup(spec, "ide", **options, context_size=8192)
        provider, = [item for item in artifacts if item.kind == "provider-config"]
        self.assertEqual(json.loads(provider.content)["language_models"]["google"]["available_models"], [{
            "name": model, "display_name": "Gemini 2.5 Flash-Lite", "max_tokens": 8192,
            "mode": {"type": "thinking", "budget_tokens": 1024},
        }])
        with self.assertRaisesRegex(WeaverError, "input limit"):
            render_model_setup(spec, "ide", **options, context_size=1048577)
        snapshot["models"][0]["output_limit"] = 1024
        with self.assertRaisesRegex(WeaverError, "below the model output limit"):
            render_model_setup(spec, "ide", **options)

    def test_cline_desktop_reasoning_handoff_uses_picker_labels(self) -> None:
        spec = get_integration("cline", ROOT)
        options = dict(root=ROOT, provider_id="openrouter", model_id="fixture/reasoner",
                       snapshot={"provider_id": "openrouter", "models": [
                           {"id": "fixture/reasoner", "reasoning": {"efforts": ["xhigh", "max", "minimal"]}},
                       ]})
        for effort, label in (("xhigh", b"Thinking level: Extra"), ("max", b"Thinking level: Max"),
                              ("minimal", b"no matching label")):
            with self.subTest(effort=effort):
                artifacts = render_model_setup(spec, "desktop", effort=effort, **options)
                self.assertIn(label, artifacts[0].content)
                self.assertIn(b"Configuration state: `manual`", artifacts[0].content)
                self.assertFalse(any(item.kind in {"model-config", "session-arguments"} for item in artifacts))
                if effort == "max":
                    self.assertIn(b"Desktop 0.0.49 does not support Max.", artifacts[0].content)

    def test_cline_cli_stages_model_and_thinking_with_explicit_approval(self) -> None:
        spec = get_integration("cline", ROOT)
        opts = dict(root=ROOT, provider_id="openrouter", model_id="fixture/reasoner",
                    snapshot={"provider_id": "openrouter", "models": [
                        {"id": "fixture/reasoner", "reasoning": {"efforts": ["high", "max"]}},
                    ]})
        for effort, tail in ((None, []), ("high", ["--thinking", "high"])):
            artifacts = render_model_setup(spec, "cli", effort=effort, **opts)
            argv = next(item for item in artifacts if item.kind == "session-arguments")
            self.assertEqual(json.loads(argv.content),
                             ["--provider", "openrouter", "--model", "fixture/reasoner", "--auto-approve", "false", *tail])
            handoff = next(item for item in artifacts if item.kind == "model-setup")
            self.assertIn(b"Keep --auto-approve false to require approval for tool calls.", handoff.content)
        artifacts = render_model_setup(spec, "cli", effort="max", **opts)
        self.assertFalse(any(item.kind == "session-arguments" for item in artifacts))

    def test_openrouter_batch_cli_stages_base_slug_and_body_reasoning_separately(self) -> None:
        from tests.test_cli_exports import invoke

        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "models.json"
            catalog.write_text(json.dumps({"data": [
                {"id": "fixture/text", "endpoints": ["/api/v1/responses"],
                 "reasoning": {"supported_efforts": ["low"]}},
                {"id": "fixture/text:batch", "endpoints": ["/api/v1/batches"],
                 "architecture": {"output_modalities": ["text"]},
                 "reasoning": {"supported_efforts": ["high"]}},
                {"id": "fixture/embed:batch", "endpoints": ["/api/v1/batches"],
                 "architecture": {"output_modalities": ["embeddings"]}},
            ]}), encoding="utf-8")
            args = ("model", "--root", str(ROOT), "--agent", "openrouter", "--provider", "openrouter",
                    "--catalog", str(catalog), "--protocol", "openrouter-batch", "--dry-run")
            for model, endpoint, effort in (("fixture/text:batch", "/v1/chat/completions", "high"),
                                           ("fixture/text", "/v1/responses", "high"),
                                           ("fixture/text", "/v1/messages", None),
                                           ("fixture/embed", "/v1/embeddings", None)):
                with self.subTest(model=model, endpoint=endpoint):
                    effort_flags = ("--reasoning-effort", effort) if effort is not None else ()
                    code, output, errors = invoke(*args, "--model", model, "--batch-endpoint", endpoint, *effort_flags)
                    self.assertEqual(code, 0, errors)
                    items = {item["kind"]: item["content"] for item in json.loads(output)["artifacts"]}
                    selector = json.loads(items["model-selection"])
                    self.assertEqual(list(selector), ["endpoint", "model"])
                    self.assertEqual(selector, {"endpoint": endpoint, "model": model.removesuffix(":batch")})
                    self.assertIn("POST https://openrouter.ai/api/v1/batches", items["model-setup"])
                    self.assertIn("before requests", items["model-setup"])
                    if effort is None:
                        self.assertNotIn("batch-reasoning", items)
                    else:
                        self.assertEqual(json.loads(items["batch-reasoning"]), {"reasoning": {"effort": effort}})
            for model, flags in (
                ("fixture/text", ()),
                ("fixture/text", ("--batch-endpoint", "/v1/embeddings")),
                ("fixture/embed", ("--batch-endpoint", "/v1/responses")),
                ("fixture/text", ("--batch-endpoint", "/v1/messages", "--reasoning-effort", "high")),
                ("fixture/text", ("--batch-endpoint", "/v1/responses", "--reasoning-effort", "low")),
                ("fixture/text:batch:batch", ("--batch-endpoint", "/v1/responses")),
                ("fixture/missing", ("--batch-endpoint", "/v1/responses")),
            ):
                with self.subTest(model=model, flags=flags):
                    code, output, _ = invoke(*args, "--model", model, *flags)
                    self.assertNotEqual(code, 0)
                    self.assertEqual(output, "")

    def test_openrouter_task_endpoint_staging_requires_saved_route_and_stages_only_model(self) -> None:
        spec = get_integration("openrouter", ROOT)
        options = {"root": ROOT, "provider_id": "openrouter", "model_id": "fixture/model"}
        routes = {
            "completions": "/api/v1/completions", "messages": "/api/v1/messages",
            "embeddings": "/api/v1/embeddings", "rerank": "/api/v1/rerank",
            "images": "/api/v1/images", "videos": "/api/v1/videos", "speech": "/api/v1/audio/speech",
            "transcriptions": "/api/v1/audio/transcriptions", "decisions": "/api/alpha/decisions",
            "extractions": "/api/alpha/extractions",
        }
        for suffix, endpoint in routes.items():
            with self.subTest(suffix=suffix):
                snapshot = {"provider_id": "openrouter", "models": [{"id": "fixture/model", "endpoints": [endpoint]}]}
                protocol = f"openrouter-{suffix}"
                artifacts = render_model_setup(spec, "gateway", protocol=protocol, snapshot=snapshot, **options)
                selection = next(item for item in artifacts if item.kind == "model-selection")
                self.assertEqual(json.loads(selection.content), {"model": "fixture/model"})
                self.assertIn(f"POST https://openrouter.ai{endpoint}", artifacts[0].content.decode())
        protocol = "openrouter-embeddings"
        snapshot = {"provider_id": "openrouter", "models": [{"id": "fixture/model", "endpoints": [routes["embeddings"]]}]}
        for extra in ({}, {"snapshot": {"provider_id": "openrouter", "models": []}},
                      {"snapshot": {"provider_id": "openrouter", "models": [{"id": "fixture/model"}]}},
                      {"snapshot": {"provider_id": "openrouter", "models": [{"id": "fixture/model", "endpoints": []}]}},
                      {"snapshot": snapshot, "effort": "low"}, {"snapshot": snapshot, "context_size": 4096}):
            with self.subTest(extra=extra), self.assertRaises(WeaverError):
                render_model_setup(spec, "gateway", protocol=protocol, **options, **extra)
        with self.assertRaisesRegex(WeaverError, "OpenRouter gateway"):
            render_model_setup(get_integration("hermes", ROOT), "cli", protocol=protocol, snapshot=snapshot, **options)

    def test_known_nontext_output_uses_manual_task_handoff_for_coding_hosts(self) -> None:
        snapshot = {"provider_id": "openrouter", "models": [{"id": "fixture/nontext-model", "architecture": {
            "input_modalities": ["text"], "output_modalities": ["embeddings"]}, "context_length": 0}]}
        for product in ("qwen-code", "kimi-code"):
            with self.subTest(product=product):
                artifacts = render_model_setup(get_integration(product, ROOT), "cli", root=ROOT,
                    provider_id="openrouter", model_id="fixture/nontext-model", snapshot=snapshot,
                    protocol="openai-responses")
                self.assertFalse(any(artifact.kind == "model-config" for artifact in artifacts))
                self.assertIn(b"Configuration state: `manual`", artifacts[0].content)
                self.assertIn(b"task-specific manual handoff", artifacts[0].content)
                self.assertIn(b"Language-agent compatibility: unknown", artifacts[0].content)

    def test_qwen_native_fragment_is_credential_reference_and_explicit_model(self) -> None:
        artifacts = render_model_setup(get_integration("qwen-code", ROOT), "cli", root=ROOT,
                                       provider_id="openai", model_id="gpt-6-sol", effort="low")
        config = next(item for item in artifacts if item.kind == "model-config")
        value = json.loads(config.content)
        route = value["modelProviders"]["openai"][0]
        self.assertEqual(route["id"], "gpt-6-sol")
        self.assertEqual(route["envKey"], "OPENAI_API_KEY")
        self.assertEqual(route["wireApi"], "responses")
        self.assertEqual(route["generationConfig"]["reasoning"], {"effort": "low"})

    def test_kimi_context_is_required_and_never_invented(self) -> None:
        spec = get_integration("kimi-code", ROOT)
        manual = render_model_setup(spec, "cli", root=ROOT, provider_id="openai", model_id="gpt-6-sol")
        self.assertIn(b"Configuration state: `manual`", manual[0].content)
        native = render_model_setup(spec, "cli", root=ROOT, provider_id="openai", model_id="gpt-6-sol", context_size=32000)
        self.assertIn(b"Configuration state: `available`", native[0].content)
        fragment = next(item for item in native if item.kind == "model-config")
        value = tomllib.loads(fragment.content.decode())
        self.assertEqual(value["models"]["weaver-model"]["max_context_size"], 32000)
        self.assertEqual(value["providers"]["weaver"]["api_key_env"], "OPENAI_API_KEY")
        snapshot = {"provider_id": "openai", "models": [{"id": "fixture/unknown-context", "context_length": 0}]}
        manual = render_model_setup(spec, "cli", root=ROOT, provider_id="openai",
                                    model_id="fixture/unknown-context", snapshot=snapshot)
        self.assertIn(b"Configuration state: `manual`", manual[0].content)
        template = next(item for item in manual if item.kind == "model-config")
        self.assertNotIn("max_context_size", tomllib.loads(template.content.decode())["models"]["weaver-model"])
        native = render_model_setup(spec, "cli", root=ROOT, provider_id="openai",
                                    model_id="fixture/unknown-context", snapshot=snapshot, context_size=32000)
        self.assertIn(b"Configuration state: `available`", native[0].content)

    def test_unsupported_hosts_and_reasoning_mode_use_explicit_manual_handoff(self) -> None:
        for product, surface, mode in (("jetbrains-air", "desktop", None), ("qwen-code", "cli", "pro"),
                                       ("chatgpt", "web", None)):
            with self.subTest(product=product):
                artifacts = render_model_setup(get_integration(product, ROOT), surface, root=ROOT,
                                               provider_id="openai", model_id="gpt-6-sol", mode=mode)
                self.assertIn(b"Configuration state: `manual`", artifacts[0].content)
                self.assertFalse(any(item.kind == "model-config" for item in artifacts))

    def test_optional_auth_local_lmstudio_routes_stay_manual_without_null_settings(self) -> None:
        for product in ("qwen-code", "kimi-code"):
            with self.subTest(product=product):
                artifacts = render_model_setup(get_integration(product, ROOT), "cli", root=ROOT,
                    provider_id="lm-studio", model_id="publisher/local-model", protocol="openai-chat", context_size=8192)
                self.assertIn(b"Configuration state: `manual`", artifacts[0].content)
                self.assertFalse(any(item.kind == "model-config" for item in artifacts))
                selected = json.loads(next(item.content for item in artifacts if item.kind == "model-selection"))
                self.assertEqual(selected["model"], "publisher/local-model")

    def test_cline_lmstudio_stages_the_provider_that_preserves_the_requested_reasoning(self) -> None:
        spec = get_integration("cline", ROOT)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lm-studio.json"
            path.write_text(json.dumps({"models": [{
                "key": "fixture/local", "type": "llm",
                "capabilities": {"reasoning": {"allowed_options": ["off", "on"], "default": "on"}},
                "loaded_instances": [{"id": "local-reader", "config": {"context_length": 8192}}],
            }]}), encoding="utf-8")
            snapshot = read_model_snapshot(path, "lm-studio", ROOT)
        options = dict(root=ROOT, provider_id="lm-studio", model_id="local-reader",
                       protocol="openai-chat", snapshot=snapshot)
        for extras, expected_provider, expected_effort in (
            ({}, "lmstudio", []),
            ({"reasoning_enabled": False}, "openai-compatible", ["--thinking", "none"]),
        ):
            with self.subTest(extras=extras):
                artifacts = render_model_setup(spec, "cli", **options, **extras)
                args, = [row for row in artifacts if row.kind == "session-arguments"]
                self.assertEqual(args.relative_path.as_posix(), "cline/model/session-arguments.json")
                self.assertEqual(json.loads(args.content), [
                    "--provider", expected_provider, "--model", "local-reader", "--auto-approve", "false",
                    *expected_effort,
                ])
                self.assertIn(f"Cline's {expected_provider} provider".encode(), artifacts[0].content)
                self.assertFalse(any(row.kind == "model-config" for row in artifacts))
        manual = render_model_setup(spec, "cli", **{**options, "snapshot": None})
        self.assertFalse(any(row.kind == "session-arguments" for row in manual))
        desktop = render_model_setup(spec, "desktop", **options)
        self.assertFalse(any(row.kind == "session-arguments" for row in desktop))

    def test_air_desktop_lmstudio_handoff_uses_an_acp_connected_agent(self) -> None:
        artifacts = render_model_setup(get_integration("jetbrains-air", ROOT), "desktop", root=ROOT,
                                       provider_id="lm-studio", model_id="publisher/local-model",
                                       protocol="openai-chat")
        self.assertEqual({item.kind for item in artifacts}, {"model-setup", "model-selection"})
        handoff = next(item.content.decode() for item in artifacts if item.kind == "model-setup")
        self.assertIn("LM Studio in an ACP-compatible agent", handoff)
        self.assertIn("controls it exposes over ACP", handoff)
        self.assertNotIn("AI > Air > Accounts", handoff)

    def test_codex_lmstudio_stages_named_responses_profile_with_explicit_effort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lm-studio.json"
            path.write_text(json.dumps({"models": [
                {"key": "fixture/reasoner", "type": "llm", "max_context_length": 32768,
                 "capabilities": {"reasoning": {"allowed_options": ["high"], "default": "high"}}},
            ]}), encoding="utf-8")
            snapshot = read_model_snapshot(path, "lm-studio", ROOT)
        spec = get_integration("codex", ROOT)
        kwargs = dict(root=ROOT, provider_id="lm-studio", model_id="fixture/reasoner",
                      snapshot=snapshot, protocol="openai-responses")
        for effort in (None, "high"):
            with self.subTest(effort=effort):
                artifacts = render_model_setup(spec, "cli", effort=effort, **kwargs)
                profile = next(item for item in artifacts if item.kind == "model-config")
                self.assertEqual(profile.relative_path.as_posix(), "codex/model/weaver-lmstudio.config.toml")
                expected = {"model": "fixture/reasoner", "model_provider": "lmstudio"}
                if effort:
                    expected["model_reasoning_effort"] = effort
                self.assertEqual(tomllib.loads(profile.content.decode()), expected)
                self.assertIn(b"codex --profile weaver-lmstudio", artifacts[0].content)
        for surface, overrides in (("desktop", {}), ("ide", {}), ("cli", {"protocol": "openai-chat"}),
                                   ("cli", {"context_size": 8192})):
            with self.subTest(surface=surface, overrides=overrides):
                artifacts = render_model_setup(spec, surface, **(kwargs | overrides))
                self.assertFalse(any(item.kind == "model-config" for item in artifacts))
        with self.assertRaises(WeaverError):
            render_model_setup(spec, "cli", effort="low", **kwargs)

    def test_claude_code_lmstudio_stages_session_settings_without_credentials(self) -> None:
        spec = get_integration("claude-code", ROOT)
        options = {"root": ROOT, "provider_id": "lm-studio", "model_id": "fixture/local",
                   "protocol": "anthropic-messages"}
        artifacts = render_model_setup(spec, "cli", **options)
        settings = next(item for item in artifacts if item.kind == "model-config")
        self.assertEqual(settings.relative_path.as_posix(), "claude-code/model/lmstudio.settings.json")
        self.assertEqual(json.loads(settings.content), {
            "model": "fixture/local",
            "env": {"ANTHROPIC_BASE_URL": "http://localhost:1234", "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"},
        })
        self.assertIn(b"--settings", artifacts[0].content)
        self.assertIn(b"ANTHROPIC_AUTH_TOKEN", artifacts[0].content)
        for overrides in ({"protocol": "openai-responses"}, {"context_size": 8192}):
            with self.subTest(overrides=overrides):
                result = render_model_setup(spec, "cli", **(options | overrides))
                self.assertFalse(any(item.kind == "model-config" for item in result))

    def test_hermes_lmstudio_stages_builtin_provider_without_implicit_context_or_auth(self) -> None:
        spec = get_integration("hermes", ROOT)
        options = {"root": ROOT, "provider_id": "lm-studio", "model_id": "fixture/local",
                   "protocol": "openai-chat",
                   "snapshot": {"provider_id": "lm-studio", "models": [
                       {"id": "fixture/local", "context_length": 32768, "lm_studio": {"type": "llm"}},
                   ]}}
        expected = {"provider": "lmstudio", "default": "fixture/local",
                    "base_url": "http://localhost:1234/v1", "api_mode": "chat_completions"}
        for context in (None, 8192):
            with self.subTest(context=context):
                artifacts = render_model_setup(spec, "cli", context_size=context, **options)
                settings = next(item for item in artifacts if item.kind == "model-config")
                self.assertEqual(settings.relative_path.as_posix(), "hermes/model/lmstudio.model.fragment.yaml")
                model = expected if context is None else expected | {"context_length": context}
                self.assertEqual(yaml.safe_load(settings.content), {"model": model})
                self.assertIn(b"LM_API_KEY separately", artifacts[0].content)
        artifacts = render_model_setup(spec, "cli", **(options | {"protocol": "openai-responses"}))
        self.assertFalse(any(item.kind == "model-config" for item in artifacts))

    def test_hermes_lmstudio_stages_a_model_scoped_reasoning_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lm-studio.json"
            path.write_text(json.dumps({"models": [{"key": "fixture/local", "type": "llm",
                "capabilities": {"reasoning": {"allowed_options": ["off", "on", "high"], "default": "on"}}}]}),
                encoding="utf-8")
            snapshot = read_model_snapshot(path, "lm-studio", ROOT)
        spec = get_integration("hermes", ROOT)
        kwargs = dict(root=ROOT, provider_id="lm-studio", model_id="fixture/local", snapshot=snapshot,
                      protocol="openai-chat")
        for selection, effort in (({"reasoning_enabled": False}, "none"), ({"effort": "high"}, "high")):
            with self.subTest(selection=selection):
                files = render_model_setup(spec, "cli", **kwargs, **selection)
                settings = next(item for item in files if item.kind == "model-config")
                config = yaml.safe_load(settings.content)
                self.assertEqual(config["agent"], {"reasoning_overrides": {"lmstudio/fixture/local": effort}})
                payload = next(item for item in files if item.kind == "model-selection")
                self.assertEqual(json.loads(payload.content)["reasoning_effort"], effort)
        with self.assertRaises(WeaverError):
            render_model_setup(spec, "cli", **kwargs, effort="low")

    def test_hermes_openrouter_stages_model_scoped_effort_for_chat_only(self) -> None:
        spec = get_integration("hermes", ROOT)
        snapshot = {"provider_id": "openrouter", "models": [
            {"id": "fixture/reasoner", "context_length": 32768, "reasoning": {"efforts": ["high"]}},
        ]}
        options = dict(root=ROOT, provider_id="openrouter", model_id="fixture/reasoner", snapshot=snapshot, effort="high")
        model = next(item for item in render_model_setup(spec, "cli", **options) if item.kind == "model-config")
        self.assertEqual(model.relative_path.as_posix(), "hermes/model/openrouter.model.fragment.yaml")
        self.assertEqual(yaml.safe_load(model.content), {
            "model": {"provider": "openrouter", "default": "fixture/reasoner",
                      "base_url": "", "api_mode": "chat_completions"},
            "agent": {"reasoning_overrides": {"openrouter/fixture/reasoner": "high"}},
        })
        for extra in ({"protocol": "openai-responses"}, {"context_size": 8192}):
            with self.subTest(extra=extra):
                artifacts = render_model_setup(spec, "cli", **(options | extra))
                self.assertFalse(any(item.kind == "model-config" for item in artifacts))

    def test_lmstudio_protocol_bases_do_not_duplicate_anthropic_version_prefix(self) -> None:
        bases = {"lmstudio-chat": "http://localhost:1234", "openai-chat": "http://localhost:1234/v1",
                 "openai-responses": "http://localhost:1234/v1", "anthropic-messages": "http://localhost:1234"}
        for product in ("qwen-code", "kimi-code"):
            for protocol, expected_base in bases.items():
                with self.subTest(product=product, protocol=protocol):
                    artifacts = render_model_setup(get_integration(product, ROOT), "cli", root=ROOT,
                        provider_id="lm-studio", model_id="publisher/local-model", protocol=protocol, context_size=8192)
                    setup = artifacts[0].content.decode()
                    self.assertIn(f"Provider API base: `{expected_base}`", setup)
                    self.assertIn("Configuration state: `manual`", setup)
                    self.assertFalse(any(item.kind == "model-config" for item in artifacts))
                    if protocol == "anthropic-messages":
                        self.assertEqual(expected_base + "/v1/messages", "http://localhost:1234/v1/messages")
                        self.assertIn("https://lmstudio.ai/docs/developer/anthropic-compat", setup)

    def test_kimi_anthropic_and_orchestrator_native_bases_match_sdk_routes(self) -> None:
        cases = (
            ("anthropic", "anthropic-messages", "anthropic", "https://api.anthropic.com"),
            ("perplexity-orchestrator", "openai-chat", "openai", "https://api.perplexity.ai/orchestrator/v1"),
            ("perplexity-orchestrator", "openai-responses", "openai_responses", "https://api.perplexity.ai/orchestrator/v1"),
            ("perplexity-orchestrator", "anthropic-messages", "anthropic", "https://api.perplexity.ai/orchestrator"),
        )
        for provider, protocol, provider_type, expected_base in cases:
            with self.subTest(provider=provider, protocol=protocol):
                artifacts = render_model_setup(get_integration("kimi-code", ROOT), "cli", root=ROOT,
                    provider_id=provider, model_id="fixture-model", protocol=protocol, context_size=8192)
                fragment = next(item for item in artifacts if item.kind == "model-config")
                configured = tomllib.loads(fragment.content.decode())["providers"]["weaver"]
                self.assertEqual(configured["type"], provider_type)
                self.assertEqual(configured["base_url"], expected_base)
                self.assertNotIn("api_key", configured)
                if provider == "perplexity-orchestrator" and protocol == "anthropic-messages":
                    self.assertEqual(configured["base_url"] + "/v1/messages",
                                     "https://api.perplexity.ai/orchestrator/v1/messages")
                    self.assertIn(b"https://docs.perplexity.ai/api-reference/gateway-messages-post", artifacts[0].content)

    def test_orchestrator_reasoning_respects_each_gateway_protocol(self) -> None:
        snapshot = {"provider_id": "perplexity-orchestrator", "models": [{"id": "fixture-model", "reasoning": {
            "state": "declared", "efforts": ["high"], "budget_tokens": {"minimum": 1},
            "toggle": True, "modes": ["pro"]}}]}
        for protocol, expected in (("openai-chat", {"reasoning_effort": "high"}),
                                   ("openai-responses", {"reasoning": {"effort": "high"}})):
            with self.subTest(protocol=protocol):
                checked = check_model(ROOT, "perplexity-orchestrator", "fixture-model", snapshot,
                                      protocol=protocol, effort="high")
                self.assertEqual(checked["reasoning_payload"], expected)
                self.assertFalse(checked["inference_tested"])
        for settings in ({"effort": "high"}, {"budget_tokens": 1024}, {"reasoning_enabled": True}, {"mode": "pro"}):
            with self.subTest(settings=settings), self.assertRaisesRegex(WeaverError, "only disabled thinking"):
                check_model(ROOT, "perplexity-orchestrator", "fixture-model", snapshot,
                            protocol="anthropic-messages", **settings)
        with self.assertRaisesRegex(WeaverError, "does not document a reasoning mode"):
            check_model(ROOT, "perplexity-orchestrator", "fixture-model", snapshot, protocol="openai-responses", mode="pro")
        checked = check_model(ROOT, "perplexity-orchestrator", "fixture-model", snapshot,
                              protocol="anthropic-messages", reasoning_enabled=False)
        self.assertEqual(checked["reasoning_payload"], {"thinking": {"type": "disabled"}})

    def test_orchestrator_openai_routes_validate_their_distinct_effort_values(self) -> None:
        provider = "perplexity-orchestrator"
        efforts = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
        snapshot = {"provider_id": provider, "models": [{"id": "fixture-model", "reasoning": {
            "efforts": efforts, "toggle": True, "on_value": "max", "off_value": "none",
        }}]}
        for effort in efforts:
            with self.subTest(effort=effort):
                chat = check_model(ROOT, provider, "fixture-model", snapshot, effort=effort, protocol="openai-chat")
                self.assertEqual(chat["reasoning_payload"], {"reasoning_effort": effort})
                if effort in {"minimal", "max"}:
                    with self.assertRaisesRegex(WeaverError, "Orchestrator Responses request schema"):
                        check_model(ROOT, provider, "fixture-model", snapshot, effort=effort, protocol="openai-responses")
                else:
                    response = check_model(ROOT, provider, "fixture-model", snapshot, effort=effort,
                                           protocol="openai-responses")
                    self.assertEqual(response["reasoning_payload"], {"reasoning": {"effort": effort}})
        with self.assertRaisesRegex(WeaverError, "Orchestrator Responses request schema"):
            check_model(ROOT, provider, "fixture-model", snapshot, reasoning_enabled=True, protocol="openai-responses")
        response = check_model(ROOT, provider, "fixture-model", snapshot,
                               reasoning_enabled=False, protocol="openai-responses")
        self.assertEqual(response["reasoning_payload"], {"reasoning": {"effort": "none"}})

    def test_orchestrator_saved_model_detail_proves_only_partial_visibility(self) -> None:
        provider = "perplexity-orchestrator"
        model = "perplexity/kimi-k3"
        price = {"input": 3, "output": 15, "cache_read": 0.3, "unit": "usd_per_1m_tokens"}
        detail = {"id": model, "object": "model", "owned_by": "perplexity", "created": 0, "pricing": price}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orchestrator-model.json"
            path.write_text(json.dumps(detail), encoding="utf-8")
            snapshot = read_model_snapshot(path, provider, ROOT)
            with self.assertRaisesRegex(WeaverError, "requires data array"):
                read_model_snapshot(path, "openai", ROOT)
        self.assertEqual(snapshot["model_count"], 1)
        self.assertEqual(snapshot["models"][0]["pricing"], price)
        self.assertEqual(snapshot["completeness"], "partial")
        self.assertFalse(snapshot["pagination"]["has_unresolved_pages"])
        checked = check_model(ROOT, provider, model, snapshot)
        self.assertTrue(checked["visible_in_snapshot"])
        self.assertEqual(checked["catalog_completeness"], "partial")
        self.assertEqual(checked["reasoning_capability"]["state"], "unknown")
        self.assertEqual(checked["availability"], "unknown")
        with self.assertRaisesRegex(WeaverError, "does not list the requested model"):
            check_model(ROOT, provider, "perplexity/glm-5.3", snapshot)
        artifacts = render_model_setup(get_integration("perplexity", ROOT), "orchestrator", root=ROOT,
                                       provider_id=provider, model_id=model, snapshot=snapshot,
                                       protocol="openai-responses")
        self.assertIn(b"partial catalog evidence", artifacts[0].content)
        self.assertIn(b"omit `previous_response_id`", artifacts[0].content)

    def test_orchestrator_allowlist_and_messages_handoff_work_from_saved_catalog(self) -> None:
        catalog = load_model_providers(ROOT)["perplexity-orchestrator"]
        models = [
            "perplexity/kimi-k3", "perplexity/glm-5.3", "perplexity/glm-5.3-flash", "perplexity/deepseek-v4-pro-0813",
        ]
        self.assertEqual([model["id"] for model in catalog["known_models"]], models)
        self.assertTrue(all(model["reasoning"] is None for model in catalog["known_models"]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orchestrator.json"
            path.write_text(json.dumps({"object": "list", "data": [{
                "id": models[0], "object": "model", "created": 0, "owned_by": "perplexity",
                "pricing": {"input": 3, "output": 15, "cache_read": 0.3, "unit": "usd_per_1m_tokens"},
            }]}), encoding="utf-8")
            snapshot = read_model_snapshot(path, catalog["id"], ROOT)
        self.assertEqual(snapshot["models"][0]["pricing"]["unit"], "usd_per_1m_tokens")
        self.assertEqual(snapshot["models"][0]["reasoning"]["state"], "unknown")
        with self.assertRaisesRegex(WeaverError, "does not list the requested model"):
            check_model(ROOT, catalog["id"], models[1], snapshot)
        with self.assertRaisesRegex(WeaverError, "effort is unsupported or unknown"):
            check_model(ROOT, catalog["id"], models[0], snapshot, protocol="openai-chat", effort="high")
        for model in models:
            for protocol in catalog["protocols"]:
                with self.subTest(model=model, protocol=protocol):
                    checked = check_model(ROOT, catalog["id"], model, protocol=protocol)
                    self.assertIsNone(checked["visible_in_snapshot"])
                    self.assertEqual(checked["availability"], "unknown")
                    self.assertEqual(checked["reasoning_payload"], {})
        spec = get_integration("perplexity", ROOT)
        artifacts = render_model_setup(spec, "orchestrator", root=ROOT, provider_id=catalog["id"], model_id=models[0],
                                       snapshot=snapshot, protocol="anthropic-messages", reasoning_enabled=False)
        self.assertIn(b"private preview", artifacts[0].content)
        body = json.loads(next(item.content for item in artifacts if item.kind == "model-selection"))
        self.assertEqual(body, {"model": models[0], "thinking": {"type": "disabled"}})
        responses = render_model_setup(spec, "orchestrator", root=ROOT, provider_id=catalog["id"], model_id=models[0],
                                       snapshot=snapshot, protocol="openai-responses")
        self.assertIn(b"Responses is stateless", responses[0].content)

    def test_qwen_anthropic_and_gemini_model_fragments_use_provider_specific_auth_and_effort(self) -> None:
        qwen = get_integration("qwen-code", ROOT)
        cases = (
            ("anthropic", "claude-opus-5", "anthropic", "claude-opus-5",
             "https://api.anthropic.com", "ANTHROPIC_API_KEY", "xhigh"),
            ("anthropic", "claude-sonnet-4-6", "anthropic", "claude-sonnet-4-6",
             "https://api.anthropic.com", "ANTHROPIC_API_KEY", "max"),
            ("google", "models/gemini-3.8-flash", "gemini", "gemini-3.8-flash",
             "https://generativelanguage.googleapis.com", "GEMINI_API_KEY", "medium"),
        )
        for provider, model, auth, qwen_model, base, env, effort in cases:
            with self.subTest(provider=provider, model=model):
                artifacts = render_model_setup(qwen, "cli", root=ROOT, provider_id=provider, model_id=model,
                                               effort=effort, context_size=100000)
                config = next(item for item in artifacts if item.kind == "model-config")
                self.assertEqual(json.loads(config.content), {
                    "modelProviders": {auth: [{"id": qwen_model, "baseUrl": base, "envKey": env,
                                              "generationConfig": {"reasoning": {"effort": effort},
                                                                   "contextWindowSize": 100000}}]},
                    "model": {"name": qwen_model},
                    "security": {"auth": {"selectedType": auth}},
                })
                request = next(item for item in artifacts if item.kind == "model-selection")
                self.assertEqual(json.loads(request.content)["model"], model)
        for provider, model, options in (
            ("google", "models/gemini-2.5-flash", {"budget_tokens": 1024}),
            ("google", "models/gemini-2.5-flash", {"reasoning_enabled": False}),
            ("anthropic", "claude-sonnet-4-6", {"budget_tokens": 4096}),
        ):
            with self.subTest(provider=provider, options=options):
                artifacts = render_model_setup(qwen, "cli", root=ROOT, provider_id=provider, model_id=model, **options)
                self.assertFalse(any(item.kind == "model-config" for item in artifacts))
                self.assertTrue(any(item.kind == "model-selection" for item in artifacts))

    def test_api_efforts_not_supported_by_host_do_not_become_invalid_host_config(self) -> None:
        qwen = render_model_setup(get_integration("qwen-code", ROOT), "cli", root=ROOT,
                                  provider_id="openai", model_id="gpt-6-sol", effort="none")
        self.assertFalse(any(item.kind == "model-config" for item in qwen))
        kimi = render_model_setup(get_integration("kimi-code", ROOT), "cli", root=ROOT,
                                  provider_id="openai", model_id="gpt-6-sol", context_size=8192, effort="low")
        fragment = next(item for item in kimi if item.kind == "model-config")
        model = tomllib.loads(fragment.content.decode())["models"]["weaver-model"]
        self.assertNotIn("none", model["support_efforts"])
        self.assertEqual(model["off_effort"], "none")

    def test_offline_operations_never_read_credentials_or_open_connections(self) -> None:
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")), \
             mock.patch.object(os, "getenv", side_effect=AssertionError("environment")):
            model_inventory(ROOT)
            check_model(ROOT, "openai", "gpt-6-sol", effort="high")
            render_model_setup(get_integration("qwen-code", ROOT), "cli", root=ROOT,
                               provider_id="openai", model_id="gpt-6-sol")


if __name__ == "__main__":
    unittest.main()
