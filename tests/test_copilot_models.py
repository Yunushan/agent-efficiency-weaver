"""Offline SDK model metadata and ordered Copilot selection validation."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentweaver.config import WeaverError
from agentweaver.copilot_models import inspect_copilot_models, validate_copilot_selection
from tests.test_cli_exports import invoke


class CopilotModelsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.snapshot = Path(directory.name) / "fixture.json"

    def write(self, rows):
        self.snapshot.write_text(json.dumps(rows), encoding="utf-8")

    def test_reports_all_models_and_keeps_missing_metadata_unknown(self):
        self.write([
            {"id": "fixture/primary", "name": "OMITTED", "policy": {"state": "enabled", "terms": "OMITTED"},
             "capabilities": {"limits": {"max_context_window_tokens": 262144, "max_prompt_tokens": 0},
                              "supports": {"reasoningEffort": True}},
             "supportedReasoningEfforts": ["low", "high"], "defaultReasoningEffort": "low"},
            {"id": "fixture/fallback", "supportedReasoningEfforts": ["high"], "defaultReasoningEffort": "unknown"},
            {"id": "fixture/blocked", "policy": {"state": "disabled"}, "supportedReasoningEfforts": []},
            {"id": "fixture/no-metadata"},
        ])
        args = ("copilot-models", "--input", str(self.snapshot), "--model", "fixture/primary",
                "--model", "fixture/fallback", "--reasoning-effort", "high")
        with mock.patch("socket.create_connection", side_effect=AssertionError("network")):
            code, out, err = invoke(*args)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["selected_models"], ["fixture/primary", "fixture/fallback"])
        self.assertEqual(result["selected_reasoning_effort"], "high")
        self.assertEqual(result["snapshot_sha256"], hashlib.sha256(self.snapshot.read_bytes()).hexdigest())
        self.assertEqual(result["snapshot_completeness"], "unknown")
        primary, fallback, blocked, unknown = result["models"]
        self.assertEqual(primary["limits"]["max_context_window_tokens"], 262144)
        self.assertIsNone(primary["limits"]["max_prompt_tokens"])
        self.assertTrue(primary["default_effort_in_list"] and primary["reasoning_effort_supported"])
        self.assertFalse(fallback["default_effort_in_list"])
        self.assertEqual(blocked["policy_state"], "disabled")
        self.assertEqual(blocked["supported_reasoning_efforts"], [])
        self.assertIsNone(unknown["supported_reasoning_efforts"])
        self.assertIsNone(unknown["policy_state"])
        self.assertNotIn("OMITTED", out)
        self.assertNotIn(str(self.snapshot), out)
        self.assertFalse(result["inference_tested"] or result["network_used"] or result["credentials_read"])

    def test_unsupported_effort_and_blocked_models_fail_selection(self):
        self.write([
            {"id": "fixture/unknown"},
            {"id": "fixture/no-reasoning", "supportedReasoningEfforts": ["high"],
             "capabilities": {"supports": {"reasoningEffort": False}}},
            {"id": "fixture/unconfigured", "policy": {"state": "unconfigured"}},
        ])
        for models, effort in (
            (["fixture/unknown"], "high"), (["fixture/no-reasoning"], "high"), (["fixture/unconfigured"], None),
            (["fixture/missing"], None), (["fixture/unknown", "fixture/unknown"], None), ([], "high"),
        ):
            with self.subTest(models=models, effort=effort), self.assertRaises(WeaverError):
                validate_copilot_selection(self.snapshot, models, effort)
        self.assertEqual(validate_copilot_selection(self.snapshot, ["fixture/unknown"], None),
                         hashlib.sha256(self.snapshot.read_bytes()).hexdigest())

    def test_rejects_inconsistent_shapes_and_discards_secret_values(self):
        for metadata in (
            {"supportedReasoningEfforts": ["high", "high"]},
            {"capabilities": {"limits": {"max_context_window_tokens": True}}},
            {"capabilities": {"supports": {"reasoningEffort": "true"}}},
            {"policy": {"state": "unexpected"}},
            {"extra": {"api_key": "NEVER-ECHO"}},
        ):
            self.write([{"id": "fixture/model", **metadata}])
            with self.subTest(metadata=metadata), self.assertRaises(WeaverError) as caught:
                inspect_copilot_models(self.snapshot)
            self.assertNotIn("NEVER-ECHO", str(caught.exception))
        self.write([{"id": "fixture/model"}, {"id": "fixture/model", "policy": {"state": "disabled"}}])
        with self.assertRaisesRegex(WeaverError, "Duplicate Copilot model ID"):
            inspect_copilot_models(self.snapshot)
