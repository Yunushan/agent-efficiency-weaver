import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cli_exports import invoke

from agentweaver.config import WeaverError
from agentweaver.factory_models import inspect_factory_models, render_factory_exec

ROOT = Path(__file__).resolve().parents[1]
MODELS = [
    {"id": "fixture-managed", "supportedReasoningEfforts": ["low", "high", "xhigh"],
     "defaultReasoningEffort": "low", "displayName": "Synthetic managed model"},
    {"id": "fixture-disabled", "disabled": True, "disabledReason": "Fixture policy",
     "supportedReasoningEfforts": ["high"]},
    {"id": "custom:vendor/review", "isCustom": True, "disabled": False,
     "supportedReasoningEfforts": ["low"]},
]


class FactoryModelTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)
        self.catalog = self.directory / "models.json"
        self.save(MODELS)

    def save(self, data):
        self.catalog.write_text(json.dumps(data), encoding="utf-8")

    def test_inventory_keeps_reasoning_and_excludes_unneeded_metadata(self):
        with mock.patch("socket.create_connection", side_effect=AssertionError("network")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process")):
            status, report, error = invoke("factory-models", "--input", str(self.catalog))
        self.assertEqual((status, error), (0, ""))
        data = json.loads(report)
        first, blocked, custom = data["models"]
        self.assertFalse(first["disabled"])
        self.assertEqual(first["supported_efforts"], ["low", "high", "xhigh"])
        self.assertEqual(first["default_effort"], "low")
        self.assertTrue(blocked["disabled"])
        self.assertTrue(custom["is_custom"])
        self.assertIn(hashlib.sha256(self.catalog.read_bytes()).hexdigest(), report)
        self.assertNotIn("Fixture policy", report)
        self.assertNotIn(str(self.catalog), report)
        self.assertIsNone(data["selection"])
        self.assertEqual(data["availability"], "unknown")

    def test_rejects_disabled_unknown_and_unsafe_inputs(self):
        for model, effort in (("missing", None), ("fixture-disabled", "high"),
                              ("fixture-managed", "max"), (None, "low")):
            with self.subTest(model=model, effort=effort), self.assertRaises(WeaverError):
                inspect_factory_models(self.catalog, model=model, effort=effort)
        for rows in ([MODELS[0], MODELS[0]], [MODELS[0] | {"disabled": "false"}],
                     [MODELS[0] | {"disabledReason": "unspecified"}],
                     [MODELS[1] | {"disabledReason": None}],
                     [MODELS[0] | {"supportedReasoningEfforts": ["low", "low"]}],
                     [MODELS[0] | {"apiKey": "NEVER-ECHO"}]):
            with self.subTest(rows=rows), self.assertRaises(WeaverError) as caught:
                self.save(rows)
                inspect_factory_models(self.catalog)
            self.assertNotIn("NEVER-ECHO", str(caught.exception))

    def test_exec_stages_exact_managed_and_custom_selectors(self):
        base = ("--root", str(ROOT), "--agent", "factory", "--surface", "cli", "--dry-run",
                "--factory-catalog", str(self.catalog))
        code, output, error = invoke("model", *base, "--model", "fixture-managed", "--reasoning-effort", "xhigh")
        self.assertEqual((code, error), (0, ""))
        items = {entry["kind"]: entry for entry in json.loads(output)["artifacts"]}
        self.assertEqual(json.loads(items["session-arguments"]["content"]),
                         ["exec", "--model", "fixture-managed", "--reasoning-effort", "xhigh"])
        self.assertEqual(json.loads(render_factory_exec(self.catalog, "fixture-managed", None)[0].content),
                         ["exec", "--model", "fixture-managed"])
        code, output, error = invoke("model", *base, "--model", "custom:vendor/review", "--reasoning-effort", "low")
        self.assertEqual((code, error), (0, ""))
        items = {entry["kind"]: entry for entry in json.loads(output)["artifacts"]}
        self.assertEqual(json.loads(items["session-arguments"]["content"]),
                         ["exec", "--model", "custom:vendor/review", "--reasoning-effort", "low"])
        self.assertIn("same custom model configuration and order", items["model-setup"]["content"])
        self.assertEqual(invoke("model", *base, "--model", "custom:vendor/review", "--reasoning-effort", "high")[0], 2)
        self.assertEqual(invoke("model", *base, "--model", "fixture-managed", "--provider", "openai")[0], 2)

    def test_custom_droid_stages_saved_ids_and_preserves_its_effort_schema(self):
        instructions = self.directory / "instructions.md"
        instructions.write_text("Review changes.", encoding="utf-8")
        base = ("--root", str(ROOT), "--agent", "factory", "--surface", "cli", "--dry-run",
                "--name", "reviewer", "--description", "Review", "--instructions-file", str(instructions),
                "--factory-catalog", str(self.catalog))
        code, output, error = invoke("agent", *base, "--model", "custom:vendor/review", "--reasoning-effort", "low")
        self.assertEqual((code, error), (0, ""))
        items = {entry["kind"]: entry for entry in json.loads(output)["artifacts"]}
        content = items["agent-definition"]["content"]
        self.assertIn('model: "custom:vendor/review"', content)
        self.assertIn('reasoningEffort: "low"', content)
        self.assertIn("Saved Factory Droid SDK", items["agent-handoff"]["content"])
        self.assertEqual(invoke("agent", *base, "--model", "fixture-managed", "--reasoning-effort", "xhigh")[0], 2)
        self.assertEqual(invoke("agent", *base, "--model", "fixture-disabled")[0], 2)
