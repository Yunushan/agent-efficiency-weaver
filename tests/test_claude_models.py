import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentweaver.claude_models import inspect_claude_models
from agentweaver.config import WeaverError
from tests.test_cli_exports import invoke

ROOT = Path(__file__).resolve().parents[1]


class ClaudeModelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.snapshot = Path(directory.name) / "models.json"
        self.rows = [
            {"value": "default", "resolvedModel": "fixture-id", "supportsEffort": True,
             "supportedEffortLevels": ["high", "max", "future"], "description": "Do not echo"},
            {"value": "opus", "resolvedModel": "fixture-id", "supportedEffortLevels": ["high"]},
            {"value": "limited", "supportsEffort": False, "supportedEffortLevels": ["high"]},
            {"value": "unknown"},
            {"value": "sonnet[1m]", "resolvedModel": "fixture-sonnet[1m]"},
        ]
        self.write(self.rows)

    def write(self, payload):
        self.snapshot.write_text(json.dumps(payload), encoding="utf-8")

    def test_inspects_exact_selectors_and_retains_unknown_metadata(self):
        with (patch("socket.create_connection", side_effect=AssertionError("network")),
              patch("subprocess.run", side_effect=AssertionError("execution"))):
            code, output, error = invoke("claude-models", "--input", str(self.snapshot),
                                         "--model", "opus", "--reasoning-effort", "high")
        self.assertEqual(code, 0, error)
        report = json.loads(output)
        self.assertEqual(report["selection"]["value"], "opus")
        self.assertEqual(report["selection"]["resolved_model"], "fixture-id")
        self.assertIn("future", report["models"][0]["supported_efforts"])
        unknown = next(entry for entry in report["models"] if entry["value"] == "unknown")
        self.assertIsNone(unknown["supported_efforts"])
        self.assertIsNone(unknown["supports_effort"])
        self.assertEqual(report["models"][-1]["resolved_model"], "fixture-sonnet[1m]")
        self.assertNotIn("Do not echo", output)
        self.assertEqual(report["snapshot_sha256"], hashlib.sha256(self.snapshot.read_bytes()).hexdigest())
        self.assertFalse(report["inference_verified"])

    def test_invalid_catalogs_and_effort_requests_fail_closed(self):
        for model, effort in (("fixture-id", None), ("limited", "high"), ("unknown", "high"),
                              ("opus", "max"), ("default", "future"), (None, "high")):
            with self.subTest(model=model, effort=effort), self.assertRaises(WeaverError):
                inspect_claude_models(self.snapshot, model=model, effort=effort)
        for payload in (self.rows + [self.rows[0]], [{"value": "opus", "supportsEffort": 1}],
                        [{"value": "opus", "supportedEffortLevels": ["low", "low"]}],
                        [{"value": "opus", "credentials": "fixture"}], {"models": self.rows}):
            with self.subTest(payload=payload):
                self.write(payload)
                with self.assertRaises(WeaverError):
                    inspect_claude_models(self.snapshot)

    def test_stages_project_settings_and_cli_max_at_the_right_scope(self):
        common = ("--root", str(ROOT), "--agent", "claude-code", "--dry-run",
                  "--claude-catalog", str(self.snapshot), "--model", "default")
        for surface in ("cli", "desktop", "vscode"):
            for extra, expected in (((), {}), (("--reasoning-effort", "high"), {"effortLevel": "high"})):
                with self.subTest(surface=surface, extra=extra):
                    code, out, err = invoke("model", *common, "--surface", surface, *extra)
                    self.assertEqual(code, 0, err)
                    artifact = json.loads(out)["artifacts"][0]
                    self.assertEqual(artifact["path"], "claude-code/.claude/settings.json")
                    self.assertEqual(json.loads(artifact["content"]), expected)
        args = ("model", *common, "--reasoning-effort", "max")
        code, out, err = invoke(*args)
        self.assertEqual(code, 0, err)
        artifact = json.loads(out)["artifacts"][0]
        self.assertEqual(artifact["kind"], "session-arguments")
        self.assertEqual(json.loads(artifact["content"]), ["--model", "default", "--effort", "max"])
        self.assertEqual(invoke(*args, "--surface", "vscode")[0], 2)
        self.assertEqual(invoke(*args, "--surface", "desktop")[0], 2)
        self.assertEqual(invoke(*args, "--provider", "anthropic")[0], 2)
        self.assertEqual(invoke(*args, "--agent", "codex")[0], 2)

    def test_custom_agents_preserve_selector_and_use_their_effort_field(self):
        prompt = self.snapshot.with_name("instructions.md")
        prompt.write_text("Review changes.", encoding="utf-8")
        common = ("agent", "--root", str(ROOT), "--agent", "claude-code", "--dry-run",
                  "--claude-catalog", str(self.snapshot), "--name", "reviewer", "--description", "Review changes.",
                  "--instructions-file", str(prompt))
        for surface in ("cli", "desktop", "vscode"):
            code, out, err = invoke(*common, "--surface", surface, "--model", "default", "--reasoning-effort", "max")
            self.assertEqual(code, 0, err)
            definition, handoff, _ = json.loads(out)["artifacts"]
            self.assertIn('model: "fixture-id"', definition["content"])
            self.assertIn("pins the model resolved by the saved session", handoff["content"])
            self.assertIn('effort: "max"', definition["content"])
            self.assertIn("Saved Claude Code ModelInfo SHA-256", handoff["content"])
        self.assertEqual(invoke(*common, "--model", "inherit")[0], 2)
        self.write([{"value": "default"}])
        self.assertEqual(invoke(*common, "--model", "default")[0], 2)
        self.write(self.rows)
        self.assertEqual(invoke(*common, "--model", "default", "--provider", "anthropic")[0], 2)
        code, out, err = invoke(*common, "--model", "sonnet[1m]")
        self.assertEqual(code, 0, err)
        self.assertIn('model: "sonnet[1m]"', json.loads(out)["artifacts"][0]["content"])

    def test_desktop_project_mcp_and_provider_handoff_use_code_tab_routes(self):
        common = ("--root", str(ROOT), "--agent", "claude-code", "--surface", "desktop", "--dry-run")
        code, out, err = invoke("render", *common, "--profile", "efficient", "--enable", "context7")
        self.assertEqual(code, 0, err)
        fragment, handoff = json.loads(out)["artifacts"]
        self.assertEqual(fragment["path"], "claude-code/.mcp.json")
        self.assertIn("context7", json.loads(fragment["content"])["mcpServers"])
        self.assertIn("local Code tab", handoff["content"])
        self.assertIn("claude_desktop_config.json", handoff["content"])
        code, out, err = invoke("provider", *common, "--provider", "openrouter", "--model", "anthropic/fixture")
        self.assertEqual(code, 0, err)
        artifacts = json.loads(out)["artifacts"]
        self.assertTrue(all(row["kind"] != "provider-config" for row in artifacts))
        self.assertIn("Configure Third-Party Inference", artifacts[0]["content"])
        self.assertIn("https://openrouter.ai/api/v1/messages", artifacts[0]["content"])
        self.assertNotIn("ANTHROPIC_BASE_URL", artifacts[0]["content"])
