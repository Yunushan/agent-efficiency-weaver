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
    render_model_setup,
)

ROOT = Path(__file__).resolve().parents[1]


class ModelCatalogTests(unittest.TestCase):
    def test_all_provider_routes_reference_registry_surfaces(self) -> None:
        providers = load_model_providers(ROOT)
        self.assertEqual(set(providers), {"openrouter", "openai", "anthropic", "google", "lm-studio",
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

    def test_lmstudio_native_reasoning_is_model_specific(self) -> None:
        snapshot = self.read("lm-studio", {"models": [{"key": "vendor/local-model", "max_context_length": 8192,
            "capabilities": {"reasoning": {"allowed_options": ["off", "on", "high"], "default": "on"}}}]})
        self.assertEqual(check_model(ROOT, "lm-studio", "vendor/local-model", snapshot,
                                     reasoning_enabled=False)["reasoning_payload"], {"reasoning": "off"})
        with self.assertRaises(WeaverError):
            check_model(ROOT, "lm-studio", "vendor/local-model", snapshot, effort="low")

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

    def test_lmstudio_nonchat_staging_requires_matching_type_and_decision_instance(self) -> None:
        snapshot = self.read("lm-studio", {"models": [
            {"key": "fixture/embed", "type": "embedding", "loaded_instances": []},
            {"key": "fixture/classifier", "type": "decision", "loaded_instances": [
                {"id": "decision-run", "config": {"context_length": 2048}},
            ]},
            {"key": "fixture/chat", "type": "llm", "loaded_instances": [
                {"id": "chat-run", "config": {}},
            ]},
        ]})
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

    def test_orchestrator_reasoning_keeps_three_distinct_wire_shapes(self) -> None:
        snapshot = {"provider_id": "perplexity-orchestrator", "models": [{"id": "fixture-model", "reasoning": {
            "state": "declared", "efforts": ["high"], "budget_tokens": {"minimum": 1}}}]}
        for protocol, expected in (("openai-chat", {"reasoning_effort": "high"}),
                                   ("openai-responses", {"reasoning": {"effort": "high"}}),
                                   ("anthropic-messages", {"output_config": {"effort": "high"}})):
            with self.subTest(protocol=protocol):
                checked = check_model(ROOT, "perplexity-orchestrator", "fixture-model", snapshot,
                                      protocol=protocol, effort="high")
                self.assertEqual(checked["reasoning_payload"], expected)
                self.assertFalse(checked["inference_tested"])
        checked = check_model(ROOT, "perplexity-orchestrator", "fixture-model", snapshot,
                              protocol="anthropic-messages", budget_tokens=1024)
        self.assertEqual(checked["reasoning_payload"], {"thinking": {"type": "enabled", "budget_tokens": 1024}})

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
