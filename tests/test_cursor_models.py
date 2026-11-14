"""Offline Cursor SDK catalog and subagent fixtures."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from agentweaver.config import WeaverError
from agentweaver.cursor_models import inspect_cursor_models
from tests.test_cli_exports import invoke

ROOT = Path(__file__).resolve().parents[1]


class CursorModelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.snapshot = Path(directory.name) / "cursor.json"
        self.instructions = Path(directory.name) / "instructions.md"
        self.instructions.write_text("Review the fixture.", encoding="utf-8")
        self.rows = [
            {"id": "reasoning-model", "displayName": "Test reasoning model",
             "aliases": ["legacy-id"],
             "parameters": [
                 {"id": "effort", "values": [{"value": "high"}, {"value": "low"}, {"value": "ultra"}]},
                 {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
             ],
             "variants": [{"displayName": "Thorough", "params": [
                 {"id": "effort", "value": "high"}, {"id": "fast", "value": "false"}],
                 "isDefault": True}]},
            {"id": "variant-only", "displayName": "Variant model",
             "variants": [{"displayName": "Deep", "params": [{"id": "reasoning", "value": "deep"}]}]},
            {"id": "ambiguous-model", "displayName": "Two reasoning controls",
             "parameters": [
                 {"id": "effort", "values": [{"value": "high"}]},
                 {"id": "reasoning", "values": [{"value": "deep"}]},
             ]},
        ]
        self.snapshot.write_text(json.dumps(self.rows), encoding="utf-8")

    def test_catalog_inventory_validates_exact_model_and_dynamic_options(self):
        code, output, errors = invoke("cursor-models", "--input", str(self.snapshot), "--model",
                                      "reasoning-model", "--reasoning-effort", "ultra", "--cursor-param", "fast=false")
        self.assertEqual(code, 0, errors)
        report = json.loads(output)
        self.assertEqual(report["models"][0]["aliases"], ["legacy-id"])
        self.assertEqual(report["models"][0]["variants"][0]["parameters"], {"effort": "high", "fast": "false"})
        self.assertEqual(report["selection"]["subagent_selector"], "reasoning-model[fast=false,effort=ultra]")
        self.assertEqual(report["snapshot_sha256"], hashlib.sha256(self.snapshot.read_bytes()).hexdigest())
        self.assertFalse(report["inference_tested"])
        report = inspect_cursor_models(self.snapshot, model="variant-only", effort="deep")
        self.assertEqual(report["selection"]["subagent_selector"], "variant-only[reasoning=deep]")
        report = inspect_cursor_models(self.snapshot, model="ambiguous-model", parameters=["reasoning=deep"])
        self.assertEqual(report["selection"]["subagent_selector"], "ambiguous-model[reasoning=deep]")

    def test_cursor_subagent_stages_options_in_model_field_on_both_surfaces(self):
        for surface in ("cli", "ide"):
            with self.subTest(surface=surface):
                args = ("agent", "--root", str(ROOT), "--agent", "cursor", "--surface", surface,
                        "--name", "reviewer", "--description", "Fixture reviewer",
                        "--instructions-file", str(self.instructions), "--dry-run")
                code, output, errors = invoke(*args, "--model", "reasoning-model",
                                              "--cursor-catalog", str(self.snapshot), "--reasoning-effort", "high")
                self.assertEqual(code, 0, errors)
                definition, handoff, _ = json.loads(output)["artifacts"]
                self.assertEqual(definition["path"], "cursor/.cursor/agents/reviewer.md")
                header = definition["content"].split("---", 2)[1]
                if surface == "cli":
                    # Cursor CLI reads the value after the first colon without YAML unquoting.
                    self.assertEqual(header, "\nname: reviewer\ndescription: Fixture reviewer\n"
                                             "model: reasoning-model[effort=high]\nreadonly: true\n")
                fields = yaml.safe_load(header)
                self.assertEqual(fields["model"], "reasoning-model[effort=high]")
                self.assertNotIn("effort", fields)
                self.assertTrue(fields["readonly"])
                self.assertIn(hashlib.sha256(self.snapshot.read_bytes()).hexdigest(), handoff["content"])
                self.assertEqual(invoke(*args, "--model", "reasoning-model", "--reasoning-effort", "high")[0], 2)

    def test_cursor_cli_preserves_description_punctuation_and_inherited_model(self):
        code, output, errors = invoke("agent", "--root", str(ROOT), "--agent", "cursor", "--surface", "cli",
                                      "--name", "reviewer", "--description", "Review: avoid #secrets",
                                      "--instructions-file", str(self.instructions), "--dry-run")
        self.assertEqual(code, 0, errors)
        content = json.loads(output)["artifacts"][0]["content"]
        self.assertIn("\ndescription: Review: avoid #secrets\nmodel: inherit\nreadonly: true\n", content)

    def test_rejects_ambiguous_unsupported_and_unsafe_selections(self):
        failures = [
            {"effort": "high"},
            {"model": "legacy-id", "effort": "high"},
            {"model": "reasoning-model", "effort": "max"},
            {"model": "reasoning-model", "effort": "high", "parameters": ["effort=low"]},
            {"model": "reasoning-model", "parameters": ["fast=false", "fast=true"]},
            {"model": "reasoning-model", "parameters": ["fast=false,effort=high"]},
            {"model": "reasoning-model", "parameters": ["fast"]},
            {"model": "variant-only", "parameters": ["reasoning=high"]},
            {"model": "ambiguous-model", "effort": "high"},
        ]
        for options in failures:
            with self.subTest(options=options), self.assertRaises(WeaverError):
                inspect_cursor_models(self.snapshot, **options)
        for rows in ([self.rows[0], self.rows[0]], [{"id": "test", "displayName": "crsr_secretfixture"}]):
            self.snapshot.write_text(json.dumps(rows), encoding="utf-8")
            code, output, errors = invoke("cursor-models", "--input", str(self.snapshot))
            self.assertEqual(code, 2)
            self.assertNotIn("crsr_secretfixture", output + errors)
