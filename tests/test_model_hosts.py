import copy
import json
import os
import socket
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from agentweaver.config import WeaverError
from agentweaver.integrations import get_integration, load_integrations
from agentweaver.models import (
    check_model,
    load_model_hosts,
    load_model_providers,
    model_inventory,
    read_model_snapshot,
    render_model_setup,
)

ROOT = Path(__file__).resolve().parents[1]


class ModelHostTests(unittest.TestCase):
    def catalog(self) -> dict:
        return json.loads((ROOT / "catalogs/model-hosts.json").read_text(encoding="utf-8"))

    def load_modified(self, catalog: dict) -> list[dict]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalogs").mkdir()
            (root / "catalogs/integrations.json").write_bytes((ROOT / "catalogs/integrations.json").read_bytes())
            (root / "catalogs/model-hosts.json").write_text(json.dumps(catalog), encoding="utf-8")
            return load_model_hosts(root)

    def test_one_explicit_row_for_every_registered_surface(self) -> None:
        expected = {(spec.id, surface.id) for spec in load_integrations(ROOT).values() for surface in spec.surfaces}
        hosts = load_model_hosts(ROOT)
        actual = {(host["product_id"], host["surface_id"]) for host in hosts}
        self.assertEqual(actual, expected)
        self.assertEqual(len(hosts), len(expected))
        self.assertEqual(hosts, sorted(hosts, key=lambda host: (host["product_id"], host["surface_id"])))
        for host in hosts:
            self.assertEqual(host["availability"], "unknown")
            self.assertEqual(host["runtime_checks"], [])
            self.assertEqual(host["fixture_tests"], ["tests/test_model_hosts.py"])

    def test_inventory_offline_without_process_credentials_or_network(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), \
                mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network forbidden")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("host processes forbidden")):
            inventory = model_inventory(ROOT)
        self.assertEqual(inventory["hosts"], load_model_hosts(ROOT))
        self.assertEqual(len(inventory["providers"]), len(load_model_providers(ROOT)))
        self.assertFalse(inventory["network_used"])
        self.assertFalse(inventory["credentials_read"])
        self.assertFalse(inventory["inference_tested"])

    def test_backend_loader_remains_independent_of_new_host_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalogs").mkdir()
            for filename in ("integrations.json", "model-providers.json"):
                (root / "catalogs" / filename).write_bytes((ROOT / "catalogs" / filename).read_bytes())
            self.assertEqual(set(load_model_providers(root)), set(load_model_providers(ROOT)))
            with self.assertRaises(WeaverError):
                model_inventory(root)

    def test_registry_pairs_cannot_be_inferred_duplicated_or_omitted(self) -> None:
        for mutation in ("unknown-product", "unknown-surface", "duplicate", "omitted"):
            with self.subTest(mutation=mutation):
                catalog = self.catalog()
                if mutation == "unknown-product":
                    catalog["hosts"][0]["product_id"] = "anthropic-code"
                elif mutation == "unknown-surface":
                    catalog["hosts"][0]["surface_id"] = "api"
                elif mutation == "duplicate":
                    catalog["hosts"].append(copy.deepcopy(catalog["hosts"][0]))
                else:
                    catalog["hosts"].pop()
                with self.assertRaises(WeaverError):
                    self.load_modified(catalog)

    def test_unknown_reasoning_is_explicit_without_exporter_or_effort_claim(self) -> None:
        hosts = {(host["product_id"], host["surface_id"]): host for host in load_model_hosts(ROOT)}
        for pair in (("jetbrains-air", "desktop"),):
            self.assertEqual(hosts[pair]["reasoning"]["state"], "unknown")
            self.assertEqual(hosts[pair]["reasoning"]["controls"], [])
        self.assertTrue(all(host["selection"]["state"] == "manual" for host in hosts.values()))
        self.assertIsNone(hosts[("copilot", "vscode")]["reasoning"]["controls"][0]["values"])
        self.assertIsNone(hosts[("kiro", "ide")]["reasoning"]["controls"][0]["values"])
        self.assertEqual(hosts[("amp", "cli")]["reasoning"]["controls"][0]["kind"], "variant")
        self.assertEqual(hosts[("devin", "desktop-local")]["reasoning"]["controls"][0]["kind"], "variant")
        self.assertNotIn("/effort", hosts[("antigravity", "ide")]["selection"]["commands"])
        self.assertNotIn("/effort", hosts[("kiro", "ide")]["selection"]["commands"])

    def test_openhands_pinned_cli_loads_manual_model_dependent_reasoning_controls(self) -> None:
        host = next(host for host in load_model_hosts(ROOT) if (host["product_id"], host["surface_id"]) == ("openhands", "cli"))
        self.assertEqual(host["selection"]["state"], "manual")
        self.assertIn("llm.model", host["selection"]["fields"])
        self.assertNotIn("unmaintained", host["selection"]["mechanism"])
        self.assertIn("CLI 1.16.0", host["selection"]["mechanism"])
        self.assertIn("SDK 1.28.1", host["selection"]["mechanism"])
        self.assertIn("subscription route", host["selection"]["mechanism"])
        self.assertIn("capped below max_output_tokens", host["selection"]["mechanism"])
        self.assertEqual(host["reasoning"]["state"], "manual")
        effort, budget = host["reasoning"]["controls"]
        self.assertEqual(effort, {"kind": "effort", "field": "llm.reasoning_effort",
                                  "values": ["low", "medium", "high", "xhigh", "none"], "model_dependent": True})
        self.assertEqual(budget, {"kind": "budget", "field": "llm.extended_thinking_budget",
                                  "values": None, "model_dependent": True})
        sources = host["reasoning"]["sources"]
        self.assertTrue(any("283c6ac3610f4299f405f7389f65766dcb83e12a/openhands_cli/stores/agent_store.py" in url for url in sources))
        self.assertTrue(any("edaac806d1599a4ee1662fd4008fbcd7b537ecd1" in url and url.endswith("llm/options/chat_options.py") for url in sources))
        self.assertEqual(host["availability"], "unknown")
        self.assertEqual(host["runtime_checks"], [])

    def test_perplexity_web_reasoning_presets_are_not_native_app_or_api_controls(self) -> None:
        host = next(host for host in load_model_hosts(ROOT) if (host["product_id"], host["surface_id"]) == ("perplexity", "app"))
        self.assertEqual(host["reasoning"]["state"], "manual")
        variant, effort = host["reasoning"]["controls"]
        self.assertEqual(variant["kind"], "variant")
        self.assertEqual(variant["values"], ["Light", "Standard", "High", "Ultra"])
        self.assertEqual(effort["kind"], "effort")
        self.assertIsNone(effort["values"])
        self.assertTrue(all("web" in control["field"] for control in (variant, effort)))
        self.assertIn("Desktop retains the regular model picker", host["selection"]["mechanism"])
        self.assertIn("custom API credentials authenticate tools", host["selection"]["mechanism"])

    def test_byok_picker_and_remote_model_are_not_arbitrary_provider_routes(self) -> None:
        hosts = {(host["product_id"], host["surface_id"]): host for host in load_model_hosts(ROOT)}
        qoder = hosts[("qoder", "cli")]["selection"]
        self.assertIn("live catalog determines", qoder["mechanism"])
        self.assertIn("https://docs.qoder.com/cli/custom-models", qoder["sources"])
        studio = hosts[("lm-studio", "desktop")]["selection"]
        self.assertIn("fixed model choices", studio["mechanism"])
        self.assertIn("not an arbitrary", hosts[("lm-studio", "bionic")]["selection"]["mechanism"])

    def test_openrouter_responses_accepts_documented_effort_without_inference_claim(self) -> None:
        snapshot = {"provider_id": "openrouter", "models": [{"id": "fixture/model", "reasoning": {
            "state": "declared", "efforts": ["low", "high"], "toggle": True,
            "budget_tokens": {"minimum": 1}, "modes": ["standard", "pro"]}}]}
        checked = check_model(ROOT, "openrouter", "fixture/model", snapshot=snapshot,
                              protocol="openai-responses", effort="high")
        self.assertEqual(checked["reasoning_payload"], {"reasoning": {"effort": "high"}})
        self.assertFalse(checked["inference_tested"])
        self.assertEqual(checked["availability"], "unknown")
        for settings in ({"budget_tokens": 1024}, {"reasoning_enabled": True}, {"mode": "pro"}):
            with self.subTest(settings=settings), self.assertRaises(WeaverError):
                check_model(ROOT, "openrouter", "fixture/model", snapshot=snapshot,
                            protocol="openai-responses", **settings)

    def test_managed_host_and_backend_catalogs_remain_separate(self) -> None:
        inventory = model_inventory(ROOT)
        api_pairs = {(provider["integration_id"], provider["surface"]) for provider in inventory["providers"]}
        for product in ("chatgpt", "copilot", "kiro"):
            self.assertFalse(any(pair[0] == product for pair in api_pairs))
            self.assertTrue(any(host["product_id"] == product for host in inventory["hosts"]))
        openai = next(host for host in inventory["hosts"] if host["product_id"] == "openai")
        self.assertIn({"kind": "mode", "field": "Responses reasoning.mode", "values": ["standard", "pro"],
                       "model_dependent": True}, openai["reasoning"]["controls"])
        google = next(host for host in inventory["hosts"] if host["product_id"] == "google")
        self.assertTrue(any("generationConfig.thinkingConfig" in control["field"] for control in google["reasoning"]["controls"]))
        self.assertTrue(any("Interactions generation_config" in control["field"] for control in google["reasoning"]["controls"]))

    def test_malformed_evidence_and_controls_fail_closed(self) -> None:
        changes = (
            ("availability", "available"), ("runtime_checks", [{"passed": True}]),
            ("fixture_tests", []), ("checked_on", "2026-02-31"),
            ("unexpected", "extra"),
        )
        for key, value in changes:
            with self.subTest(key=key):
                catalog = self.catalog()
                catalog["hosts"][0][key] = value
                with self.assertRaises(WeaverError):
                    self.load_modified(catalog)
        for mutation in ("http", "credentials", "state-type", "control-kind", "control-values", "not-model-dependent", "duplicate", "unknown-with-control"):
            with self.subTest(mutation=mutation):
                catalog = self.catalog()
                row = catalog["hosts"][0]
                control = row["reasoning"]["controls"][0]
                if mutation == "http":
                    row["selection"]["sources"] = ["http://example.com/docs"]
                elif mutation == "credentials":
                    row["selection"]["sources"] = ["https://user:password@example.com/docs"]
                elif mutation == "state-type":
                    row["selection"]["state"] = []
                elif mutation == "control-kind":
                    control["kind"] = []
                elif mutation == "control-values":
                    control["values"] = ["high", "high"]
                elif mutation == "not-model-dependent":
                    control["model_dependent"] = False
                elif mutation == "duplicate":
                    row["reasoning"]["controls"].append(copy.deepcopy(control))
                else:
                    row["reasoning"]["state"] = "unknown"
                with self.assertRaises(WeaverError):
                    self.load_modified(catalog)

    def test_malformed_anthropic_nested_capabilities_raise_controlled_error(self) -> None:
        capabilities = [
            {"thinking": {"types": {"adaptive": True}}},
            {"thinking": {"types": {"disabled": False}}},
            {"thinking": {"types": {"enabled": {"supported": "yes"}}}},
            {"thinking": {"types": []}},
            {"thinking": True},
            {"effort": {"high": True}},
            {"effort": {"max": {"supported": "yes"}}},
            {"effort": []},
        ]
        for caps in capabilities:
            with self.subTest(caps=caps), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "snapshot.json"
                path.write_text(json.dumps({"data": [{"id": "model-one", "capabilities": caps}]}), encoding="utf-8")
                with self.assertRaises(WeaverError):
                    read_model_snapshot(path, "anthropic", ROOT)

    def test_explicit_effort_survives_compatible_enabled_toggle(self) -> None:
        for protocol, expected in (("openai-responses", {"reasoning": {"effort": "high"}}),
                                   ("openai-chat", {"reasoning_effort": "high"})):
            checked = check_model(ROOT, "openai", "gpt-6-sol", protocol=protocol,
                                  effort="high", reasoning_enabled=True)
            self.assertEqual(checked["reasoning_payload"], expected)
        with self.assertRaises(WeaverError):
            check_model(ROOT, "openai", "gpt-6-sol", effort="none", reasoning_enabled=True)
        with self.assertRaises(WeaverError):
            check_model(ROOT, "openai", "gpt-6-sol", mode="pro", reasoning_enabled=False)

    def test_snapshot_toggle_mapping_cannot_bypass_effort_or_type_validation(self) -> None:
        for provider, on_value, off_value, enabled in (("openai", "invalid-wire-effort", "none", True),
                                                     ("openai", "high", "none", True),
                                                     ("openai", "low", "high", False),
                                                     ("anthropic", "invalid-thinking-type", "disabled", True)):
            with self.subTest(provider=provider, on_value=on_value, enabled=enabled):
                snapshot = {"provider_id": provider, "models": [{"id": "fixture-model", "reasoning": {
                    "state": "declared", "efforts": ["low"], "toggle": True,
                    "on_value": on_value, "off_value": off_value}}]}
                with self.assertRaises(WeaverError):
                    check_model(ROOT, provider, "fixture-model", snapshot=snapshot, reasoning_enabled=enabled)

    def test_public_normalized_snapshot_provenance_is_validated(self) -> None:
        bad_provenance = [
            {"source_url": "https://person:credential@example.com/models"},
            {"source_url": "https://example.com/models?api_key=example"},
            {"source_url": "C:\\private\\models.json"},
            {"local_path": "C:\\private\\models.json"},
            {"sha256": "invalid"}, {"origin": "runtime-tested"}, {"scope": []},
            {"captured_on": "2026-02-31"}, "not-an-object",
        ]
        for provenance in bad_provenance:
            with self.subTest(provenance=provenance), self.assertRaises(WeaverError):
                check_model(ROOT, "openai", "example-model", snapshot={
                    "provider_id": "openai", "models": [{"id": "example-model"}], "provenance": provenance})
        valid = {"source_url": "https://api.openai.com/v1/models", "sha256": "a" * 64,
                 "origin": "caller-supplied", "captured_on": "2026-10-26", "auth_scope": "project"}
        checked = check_model(ROOT, "openai", "example-model", snapshot={
            "provider_id": "openai", "models": [{"id": "example-model"}], "provenance": valid})
        self.assertEqual(checked["snapshot_provenance"], valid)
        self.assertIsNot(checked["snapshot_provenance"], valid)

    def test_qwen_chat_max_uses_manual_handoff_without_unverified_profile(self) -> None:
        snapshot = {"provider_id": "openrouter", "models": [{"id": "fixture/custom-route", "reasoning": {
            "state": "declared", "efforts": ["high", "max"], "budget_tokens": None,
            "toggle": False, "modes": None}}]}
        artifacts = render_model_setup(get_integration("qwen-code", ROOT), "cli", root=ROOT,
                                       provider_id="openrouter", model_id="fixture/custom-route", snapshot=snapshot,
                                       protocol="openai-chat", effort="max")
        self.assertFalse(any(artifact.kind == "model-config" for artifact in artifacts))
        setup = next(artifact for artifact in artifacts if artifact.kind == "model-setup")
        self.assertIn("Configuration state: `manual`", setup.content.decode())
        selection = next(artifact for artifact in artifacts if artifact.kind == "model-selection")
        self.assertEqual(json.loads(selection.content), {"model": "fixture/custom-route", "reasoning_effort": "max"})


if __name__ == "__main__":
    unittest.main()
