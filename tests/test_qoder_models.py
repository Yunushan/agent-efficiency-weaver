"""Offline fixtures for the Qoder Agent SDK model catalog and staging."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml
from test_cli_exports import ROOT, invoke

from agentweaver.config import WeaverError
from agentweaver.qoder_models import inspect_qoder_models


class QoderModelsTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "models.json"
        self.prompt = Path(scratch.name) / "prompt.md"
        self.prompt.write_text("Review the patch.", encoding="utf-8")
        self.listing = [
            {"value": "fixture-nested", "isEnabled": True, "displayName": "Fixture Model Pro (Beta)",
             "description": "omit this text",
             "thinking_config": {"enabled": {"is_default": True, "efforts": {
                 "high": {"is_default": True}, "future-effort": {},
             }}, "disabled": {}},
             "context_config": {"400K": {"token_count": 400000, "is_default": True}}},
            {"value": "fixture-flat", "isEnabled": True,
             "efforts": ["medium", "max"], "defaultEffort": "medium", "supportsDisabled": False,
             "availableContextWindows": [200000, 400000], "defaultContextWindow": 200000},
            {"value": "fixture-disabled", "isEnabled": False},
            {"value": "fixture-unknown"},
        ]
        self.save(self.listing)

    def save(self, values):
        self.path.write_text(json.dumps(values), encoding="utf-8")

    def test_reports_nested_and_flat_metadata_and_preserves_unknowns(self):
        status, out, err = invoke("qoder-models", "--input", str(self.path))
        self.assertEqual(status, 0, err)
        self.assertNotIn("omit this text", out)
        data = json.loads(out)
        nested, flat, disabled, unknown = data["models"]
        self.assertEqual(nested["display_name"], "Fixture Model Pro (Beta)")
        self.assertIsNone(flat["display_name"])
        self.assertEqual(nested["efforts"], [
            {"effort": "high", "is_default": True}, {"effort": "future-effort", "is_default": None},
        ])
        self.assertTrue(nested["thinking_disabled_advertised"])
        self.assertFalse(flat["thinking_disabled_advertised"])
        self.assertEqual(flat["context_windows"], [
            {"label": None, "token_count": 200000, "is_default": True},
            {"label": None, "token_count": 400000, "is_default": False},
        ])
        self.assertFalse(disabled["enabled"])
        self.assertIsNone(unknown["enabled"])
        self.assertIsNone(unknown["efforts"])
        self.assertEqual(data["availability"], "unknown")
        self.assertFalse(data["inference_verified"])

    def test_rejects_unconfirmed_models_unsupported_parameters_and_malformed_metadata(self):
        for values in ({"model": "missing"}, {"model": "fixture-disabled"}, {"model": "fixture-unknown"},
                       {"model": "fixture-flat", "effort": "off"},
                       {"model": "fixture-nested", "context_size": 1_000_000}, {"effort": "high"}):
            with self.subTest(values=values), self.assertRaises(WeaverError):
                inspect_qoder_models(self.path, **values)
        for bad in ({"isEnabled": 1}, {"availableContextWindows": [True]}, {"supportsDisabled": "yes"},
                    {"thinking_config": {"enabled": {"efforts": {"high": False}}}},
                    {"serverModel": {"authorization": "synthetic"}}):
            with self.subTest(bad=bad), self.assertRaises(WeaverError):
                self.save([self.listing[0] | bad])
                inspect_qoder_models(self.path)
        self.save([self.listing[0]] * 2)
        with self.assertRaisesRegex(WeaverError, "Duplicate"):
            inspect_qoder_models(self.path)

    def test_catalog_constrains_session_settings_and_subagent_exports(self):
        args = ("--root", str(ROOT), "--agent", "qoder", "--model", "fixture-nested",
                "--qoder-catalog", str(self.path), "--dry-run")
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        status, out, err = invoke("model", *args, "--reasoning-effort", "high", "--context-size", "400000")
        self.assertEqual(status, 0, err)
        self.assertIn(digest, out)
        self.assertEqual(invoke("model", *args, "--reasoning-effort", "low")[0], 2)
        self.assertEqual(invoke("model", *args, "--reasoning-effort", "future-effort")[0], 2)
        self.assertEqual(invoke("model", *args, "--qoder-settings", "--reasoning-effort", "off")[0], 0)
        agent = (*args, "--name", "reviewer", "--description", "Review code", "--instructions-file", str(self.prompt))
        status, out, err = invoke("agent", *agent, "--reasoning-effort", "high")
        self.assertEqual(status, 0, err)
        artifacts = {item["kind"]: item["content"] for item in json.loads(out)["artifacts"]}
        self.assertIn(digest, out)
        metadata = next(yaml.safe_load_all(artifacts["agent-definition"].split("---\n", 2)[1]))
        self.assertEqual((metadata["model"], metadata["effort"]), ("fixture-nested", "high"))
        self.assertEqual(invoke("agent", *agent, "--reasoning-effort", "low")[0], 2)
        self.assertEqual(invoke("agent", *agent, "--provider", "openrouter")[0], 2)

    def test_ide_subagent_uses_documented_display_name_and_model_link(self):
        args = ("agent", "--root", str(ROOT), "--agent", "qoder", "--surface", "ide", "--dry-run",
                "--name", "reviewer", "--description", "Review code", "--instructions-file", str(self.prompt))
        selector = ("--model", "fixture-nested", "--qoder-catalog", str(self.path))
        status, out, err = invoke(*args, *selector)
        self.assertEqual(status, 0, err)
        definition = json.loads(out)["artifacts"][0]["content"]
        data = yaml.safe_load(definition.split("---\n", 2)[1])
        self.assertEqual(data["model"], "[Fixture Model Pro (Beta)](fixture-nested)")
        self.assertEqual(data["mcpServers"], [])
        self.assertNotIn("effort", data)
        self.assertIn(hashlib.sha256(self.path.read_bytes()).hexdigest(), out)
        self.assertEqual(invoke(*args, *selector, "--reasoning-effort", "high")[0], 2)
        self.assertEqual(invoke(*args, "--model", "fixture-nested")[0], 2)
        for bad in (None, "", "Name] (other)", "Fixture\\Model", "Name\nnew: field"):
            with self.subTest(display_name=bad):
                self.save([self.listing[0] | {"displayName": bad}])
                self.assertEqual(invoke(*args, *selector)[0], 2)
        self.save([self.listing[0] | {"isEnabled": False}])
        self.assertEqual(invoke(*args, *selector)[0], 2)
