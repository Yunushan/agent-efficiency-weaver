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

    def test_cli_stages_thinking_modes_and_budget_as_session_arguments(self):
        base = ("model", "--root", str(ROOT), "--agent", "qoder", "--surface", "cli", "--dry-run",
                "--qoder-catalog", str(self.path))
        for options, native in (
            (("--reasoning-mode", "adaptive", "--reasoning-effort", "high"),
             ["--reasoning-effort", "high", "--thinking", "adaptive"]),
            (("--reasoning-mode", "disabled"), ["--thinking", "disabled"]),
            (("--reasoning-budget", "2048"), ["--thinking-budget", "2048"]),
        ):
            with self.subTest(options=options):
                code, out, err = invoke(*base, "--model", "fixture-nested", *options)
                self.assertEqual(code, 0, err)
                artifacts = {item["kind"]: item["content"] for item in json.loads(out)["artifacts"]}
                self.assertEqual(json.loads(artifacts["session-arguments"]), ["--model", "fixture-nested", *native])
                self.assertIn("does not declare supported modes or fixed-budget ranges", artifacts["model-setup"])
        for model, options in (
            ("fixture-flat", ("--reasoning-mode", "disabled")),
            ("fixture-nested", ("--reasoning-mode", "invalid")),
            ("fixture-nested", ("--reasoning-budget", "9007199254740992")),
            ("fixture-nested", ("--reasoning-budget", "2048", "--reasoning-mode", "adaptive")),
            ("fixture-nested", ("--reasoning-mode", "auto", "--qoder-settings")),
            ("fixture-nested", ("--reasoning-budget", "2048", "--qoder-settings")),
        ):
            with self.subTest(model=model, options=options):
                self.assertEqual(invoke(*base, "--model", model, *options)[0], 2)
        self.save([{"value": "fixture-toggle-off", "isEnabled": True, "thinking_config": {"disabled": {}}}])
        self.assertEqual(invoke(*base, "--model", "fixture-toggle-off", "--reasoning-mode", "enabled")[0], 2)
        self.assertEqual(invoke(*base, "--model", "fixture-toggle-off", "--reasoning-budget", "2048")[0], 2)

    def test_ide_subagent_can_reference_selected_skills_and_allowlisted_servers(self):
        base = ("agent", "--root", str(ROOT), "--agent", "qoder", "--dry-run", "--name", "reviewer",
                "--description", "Review code", "--instructions-file", str(self.prompt))
        ide = (*base, "--surface", "ide")
        opt_in = ("--allow-skill", "security-review", "--mcp-profile", "efficient", "--allow-mcp", "context7")
        selector = ("--model", "fixture-nested", "--qoder-catalog", str(self.path))
        for model in ((), selector):
            with self.subTest(model=model):
                status, out, err = invoke(*ide, *opt_in, *model)
                self.assertEqual(status, 0, err)
                artifacts = {item["kind"]: item["content"] for item in json.loads(out)["artifacts"]}
                data = yaml.safe_load(artifacts["agent-definition"].split("---\n", 2)[1])
                self.assertEqual(data["tools"], "Read, Grep, Glob")
                self.assertEqual(data["skills"], ["security-review"])
                self.assertEqual(data["mcpServers"], ["context7"])
                self.assertEqual("model" in data, bool(model))
                self.assertNotIn("https://", artifacts["agent-definition"])
                self.assertIn("contains names only", artifacts["agent-handoff"])
                self.assertNotIn("mcp", artifacts)
        for flags in (
            ("--allow-skill", "absent-skill"),
            ("--allow-skill", "security-review", "--allow-skill", "security-review"),
            ("--allow-mcp", "context7"),
            ("--mcp-profile", "efficient"),
            ("--mcp-profile", "../efficient", "--allow-mcp", "context7"),
            ("--mcp-profile", "efficient", "--allow-mcp", "github"),
            ("--mcp-profile", "efficient", "--allow-mcp", "context7", "--allow-mcp", "context7"),
            ("--mcp-profile", "efficient", "--profile-mcp", "context7"),
        ):
            with self.subTest(flags=flags):
                self.assertEqual(invoke(*ide, *flags)[0], 2)
        self.assertEqual(invoke(*ide, *opt_in, "--allow-mcp-tool", "context7:resolve-library-id")[0], 2)

    def test_cli_subagent_restricts_mcp_to_explicit_tools_on_selected_servers(self):
        args = ("agent", "--root", str(ROOT), "--agent", "qoder", "--surface", "cli", "--dry-run",
                "--name", "reviewer", "--description", "Review code", "--instructions-file", str(self.prompt))
        selector = ("--model", "fixture-nested", "--qoder-catalog", str(self.path), "--reasoning-effort", "high")
        context = ("--allow-skill", "security-review", "--mcp-profile", "efficient", "--allow-mcp", "context7")
        tool = ("--allow-mcp-tool", "context7:resolve-library-id")
        for flags, tools in (((), []), (tool, ["mcp__context7__resolve-library-id"])):
            with self.subTest(tools=tools):
                code, out, error = invoke(*args, *context, *selector, *flags)
                self.assertEqual(code, 0, error)
                artifacts = {item["kind"]: item["content"] for item in json.loads(out)["artifacts"]}
                meta = yaml.safe_load(artifacts["agent-definition"].split("---\n", 2)[1])
                self.assertEqual(meta, {"name": "reviewer", "description": "Review code",
                                        "tools": ["Read", "Grep", "Glob", *tools], "skills": ["security-review"],
                                        "mcpServers": ["context7"], "model": "fixture-nested", "effort": "high"})
                self.assertNotIn("mcp", artifacts)
        for flags in (tool + tool, ("--allow-mcp-tool", "context7:*"), ("--allow-mcp-tool", "github:search"),
                      ("--allow-mcp-tool", "mcp__context7__resolve-library-id"),
                      ("--allow-mcp-tool", "context7:resolve-library-id,Write")):
            with self.subTest(flags=flags):
                self.assertEqual(invoke(*args, *context, *flags)[0], 2)
        self.assertEqual(invoke(*args, *tool)[0], 2)

    def test_cli_subagent_stages_a_numeric_effort_with_catalog_provenance(self):
        base = ("agent", "--root", str(ROOT), "--agent", "qoder", "--dry-run", "--name", "reviewer",
                "--description", "Review code", "--instructions-file", str(self.prompt))
        model = ("--model", "fixture-nested", "--qoder-catalog", str(self.path))
        budget = ("--reasoning-budget", "2048")
        args = (*base, "--surface", "cli")
        code, output, error = invoke(*args, *model, *budget)
        self.assertEqual(code, 0, error)
        artifacts = {item["kind"]: item["content"] for item in json.loads(output)["artifacts"]}
        meta = yaml.safe_load(artifacts["agent-definition"].split("---\n", 2)[1])
        self.assertIs(type(meta["effort"]), int)
        self.assertEqual(meta["effort"], 2048)
        self.assertEqual(meta["model"], "fixture-nested")
        self.assertIn(hashlib.sha256(self.path.read_bytes()).hexdigest(), artifacts["agent-handoff"])
        self.assertIn("does not declare fixed-budget ranges", artifacts["agent-handoff"])
        for opts in (
            ("--model", "fixture-nested", *budget),
            ("--qoder-catalog", str(self.path), *budget),
            (*model, *budget, "--reasoning-effort", "high"),
            (*model, "--reasoning-budget", "0"),
            (*model, "--reasoning-budget", "9007199254740992"),
            ("--model", "fixture-disabled", "--qoder-catalog", str(self.path), *budget),
        ):
            with self.subTest(opts=opts):
                self.assertEqual(invoke(*args, *opts)[0], 2)
        self.assertEqual(invoke(*base, "--surface", "ide", *model, *budget)[0], 2)
        self.save([{"value": "fixture-nested", "isEnabled": True, "thinking_config": {"disabled": {}}}])
        self.assertEqual(invoke(*args, *model, *budget)[0], 2)
