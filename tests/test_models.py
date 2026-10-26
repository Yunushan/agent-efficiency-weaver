import json
import os
import socket
import tempfile
import tomllib
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

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


class ModelValidationTests(unittest.TestCase):
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
    def test_known_nontext_output_uses_manual_task_handoff_for_coding_hosts(self) -> None:
        snapshot = {"provider_id": "openrouter", "models": [{"id": "fixture/nontext-model", "architecture": {
            "input_modalities": ["text"], "output_modalities": ["embeddings"]}}]}
        for product in ("qwen-code", "kimi-code"):
            with self.subTest(product=product):
                artifacts = render_model_setup(get_integration(product, ROOT), "cli", root=ROOT,
                    provider_id="openrouter", model_id="fixture/nontext-model", snapshot=snapshot,
                    protocol="openai-responses", context_size=8192)
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
