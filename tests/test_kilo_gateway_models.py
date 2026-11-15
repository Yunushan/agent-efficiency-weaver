import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agentweaver.config import WeaverError
from agentweaver.kilo_gateway_models import select_kilo_gateway_model
from agentweaver.models import read_model_snapshot
from tests.test_cli_exports import invoke

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = "vendor/fixture"
ROW = {
    "id": CANDIDATE,
    "name": "Fixture",
    "alias_target": "vendor/fixture-release",
    "description": "An example of task-specific public prose.",
    "context_length": 8192,
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "pricing": {"prompt": "-1", "completion": "0.00002"},
    "supported_parameters": ["reasoning", "tools"],
    "opencode": {
        "ai_sdk_provider": "anthropic",
        "reasoning_options": [{"type": "toggle"}, {"type": "effort", "values": ["none", "max"]}],
        "variants": {
            "instant": {"reasoning": {"enabled": False, "effort": "none"}},
            "reasoning-max": {"reasoning": {"enabled": True, "effort": "max"}, "verbosity": "max"},
        },
    },
}


class KiloGatewayModelTests(unittest.TestCase):
    def test_snapshot_preserves_exact_gateway_controls_and_unknown_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({"data": [ROW, {"id": "vendor/second"}]}), encoding="utf-8")
            report = read_model_snapshot(path, "kilo-gateway", ROOT)
            self.assertEqual(report["model_count"], 2)
            self.assertEqual(report["completeness"], "unknown")
            self.assertFalse(report["network_used"])
            self.assertEqual(report["provenance"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            model, optional = report["models"]
            self.assertEqual(model["kilo_gateway"], ROW["opencode"] | {"alias_target": ROW["alias_target"]})
            self.assertEqual(model["pricing"], {"prompt": None, "completion": "0.00002"})
            self.assertEqual(model["pricing_observations"], [{"field": "prompt", "raw": "-1", "state": "unknown"}])
            self.assertEqual(optional["kilo_gateway"], {"variants": {}, "reasoning_options": []})
            self.assertNotIn("task-specific", json.dumps(report))
            self.assertEqual(select_kilo_gateway_model(report, CANDIDATE, "instant"), {
                "model": CANDIDATE, "variant": "instant",
                "advertised_variant_settings": {"reasoning": {"enabled": False, "effort": "none"}},
            })
            for model_id, variant in (("vendor/missing", None), (CANDIDATE, "max"), ("vendor/second", "instant")):
                with self.subTest(model=model_id, variant=variant), self.assertRaises(WeaverError):
                    select_kilo_gateway_model(report, model_id, variant)

    def test_snapshot_rejects_malformed_options_and_credential_fields(self):
        opencode = ROW["opencode"]
        mutations = (
            {"opencode": opencode | {"reasoning_options": [{"type": "effort", "values": ["bogus"]}]}},
            {"opencode": opencode | {"variants": {"instant": {"reasoning": {"enabled": "false", "effort": "none"}}}}},
            {"opencode": opencode | {"headers": {"X-Example": "FIXTURE-ONLY"}}},
            {"description": {"authorization": "FIXTURE-ONLY"}},
            {"pricing": {"prompt": "-10"}},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            for mutation in mutations:
                with self.subTest(mutation=mutation), self.assertRaises(WeaverError) as caught:
                    path.write_text(json.dumps({"data": [ROW | mutation]}), encoding="utf-8")
                    read_model_snapshot(path, "kilo-gateway", ROOT)
                self.assertNotIn("FIXTURE-ONLY", str(caught.exception))

    def test_cli_stages_catalog_selections_and_keeps_host_routes_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({"data": [ROW]}), encoding="utf-8")
            inventory = ("models", "--root", str(ROOT), "--provider", "kilo-gateway", "--input", str(path))
            code, output, error = invoke(*inventory)
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["models"][0]["id"], CANDIDATE)
            base = ("model", "--root", str(ROOT), "--agent", "kilo", "--model", CANDIDATE, "--dry-run",
                    "--catalog", str(path), "--provider", "kilo-gateway")
            for args, expected in (((), {"model": CANDIDATE}),
                                   (("--model-variant", "reasoning-max"), {
                                       "model": CANDIDATE, "variant": "reasoning-max",
                                       "advertised_variant_settings": ROW["opencode"]["variants"]["reasoning-max"],
                                   })):
                code, output, error = invoke(*base, "--surface", "gateway", *args)
                self.assertEqual((code, error), (0, ""))
                artifacts = json.loads(output)["artifacts"]
                self.assertEqual(artifacts[0]["path"], "kilo/gateway/model-selection.json")
                self.assertEqual(json.loads(artifacts[0]["content"]), expected)
                self.assertIn("Saved Kilo Gateway catalog SHA-256", artifacts[1]["content"])
            for flags in (("--surface", "gateway", "--reasoning-effort", "max"),
                          ("--surface", "gateway", "--model-variant", "max")):
                self.assertEqual(invoke(*base, *flags)[0], 2)
            host = ("provider", "--root", str(ROOT), "--agent", "kilo", "--provider", "openrouter",
                    "--model", CANDIDATE, "--dry-run")
            for surface in ("cli", "ide", "desktop"):
                self.assertEqual(invoke(*host, "--surface", surface)[0], 0)
            self.assertEqual(invoke(*host, "--surface", "gateway")[0], 2)

    def test_clients_stage_exact_gateway_selectors_and_enforce_chat_eligibility(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({"data": [ROW]}), encoding="utf-8")
            base = ("model", "--root", str(ROOT), "--agent", "kilo", "--model", CANDIDATE, "--dry-run",
                    "--catalog", str(path), "--provider", "kilo-gateway")
            for surface in ("cli", "ide", "desktop"):
                with self.subTest(surface=surface):
                    code, output, error = invoke(*base, "--surface", surface, "--model-variant", "instant")
                    self.assertEqual((code, error), (0, ""))
                    files = {item["path"]: item["content"] for item in json.loads(output)["artifacts"]}
                    chosen = json.loads(files["kilo/model/gateway-selection.json"])
                    self.assertEqual(chosen["model"], CANDIDATE)
                    self.assertEqual(chosen["client_model"], f"kilo/{CANDIDATE}")
                    self.assertEqual(chosen["variant"], "instant")
                    arguments = files.get("kilo/model/session-arguments.json")
                    if surface == "cli":
                        self.assertEqual(json.loads(arguments), [
                            "run", "--model", f"kilo/{CANDIDATE}", "--variant=instant",
                        ])
                    else:
                        self.assertIsNone(arguments)
            code, output, error = invoke(*base, "--surface", "cli")
            self.assertEqual((code, error), (0, ""))
            files = {item["kind"]: item["content"] for item in json.loads(output)["artifacts"]}
            self.assertEqual(json.loads(files["session-arguments"]), ["run", "--model", f"kilo/{CANDIDATE}"])
            for narrower in ({"supported_parameters": ["reasoning"]},
                             {"architecture": {"output_modalities": ["image"]}}):
                with self.subTest(narrower=narrower):
                    path.write_text(json.dumps({"data": [ROW | narrower]}), encoding="utf-8")
                    self.assertEqual(invoke(*base, "--surface", "cli")[0], 2)
                    self.assertEqual(invoke(*base, "--surface", "desktop")[0], 2)
                    self.assertEqual(invoke(*base, "--surface", "gateway")[0], 0)
            for parameters in ([], None):
                row = dict(ROW)
                if parameters is None:
                    del row["supported_parameters"]
                else:
                    row["supported_parameters"] = parameters
                path.write_text(json.dumps({"data": [row]}), encoding="utf-8")
                self.assertEqual(invoke(*base, "--surface", "cli")[0], 0)
