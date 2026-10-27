"""Perplexity Computer's account-scoped discovery is an offline MCP snapshot."""
import copy
import hashlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cli_exports import invoke

from agentweaver.computer_models import inspect_computer_models
from agentweaver.config import WeaverError

SYNTHETIC = {
    "default_mode": "standard",
    "default_model": "fixture/model-b",
    "modes": [{"mode": "standard", "model": "fixture/model-b", "effort": "high"},
              {"mode": "light", "model": "fixture/model-a", "effort": "low"}],
    "models": [{"id": "fixture/model-a", "label": "A", "description": "Synthetic fixture",
                "fast": True, "efforts": ["low", "medium"], "default_effort": "low"},
               {"id": "fixture/model-b", "label": "B", "description": "Synthetic fixture",
                "fast": False, "efforts": ["high", "xhigh"], "default_effort": "high"}],
}


class ComputerModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.json"
        self.write(SYNTHETIC)

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_preserves_account_scoped_models_modes_and_defaults_without_inference(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")):
            result = inspect_computer_models(self.path)
        self.assertEqual(result["models"], SYNTHETIC["models"])
        self.assertEqual(result["modes"], SYNTHETIC["modes"])
        self.assertEqual(result["default_mode"], "standard")
        self.assertEqual(result["default_model"], "fixture/model-b")
        self.assertEqual(result["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(result["selection_fields"], {})
        self.assertEqual(result["snapshot_completeness"], "unknown")
        self.assertFalse(result["network_used"] or result["credentials_read"] or result["inference_tested"])
        self.assertNotIn(str(self.path), json.dumps(result))

    def test_selection_requires_exact_account_snapshot_model_mode_and_effort(self):
        result = inspect_computer_models(self.path, model="fixture/model-a", effort="medium")
        self.assertEqual(result["selection_fields"], {"model": "fixture/model-a", "effort": "medium"})
        self.assertEqual(inspect_computer_models(self.path, mode="light")["selection_fields"], {"mode": "light"})
        self.assertEqual(inspect_computer_models(self.path, model="fixture/model-b")["selection_fields"],
                         {"model": "fixture/model-b"})
        for args in ({"model": "fixture/model-b", "effort": "low"}, {"mode": "missing"},
                     {"model": "fixture/missing"}, {"mode": "light", "model": "fixture/model-a"},
                     {"effort": "high"}, {"mode": "light", "effort": "low"}):
            with self.subTest(args=args), self.assertRaises(WeaverError):
                inspect_computer_models(self.path, **args)

    def test_missing_efforts_are_unknown_and_model_specific_effort_cannot_be_assumed(self):
        data = copy.deepcopy(SYNTHETIC)
        del data["models"][0]["efforts"]
        data["models"][0]["default_effort"] = None
        self.write(data)
        result = inspect_computer_models(self.path, model="fixture/model-a")
        self.assertIsNone(result["models"][0]["efforts"])
        with self.assertRaises(WeaverError):
            inspect_computer_models(self.path, model="fixture/model-a", effort="low")

    def test_preserves_all_model_rows_and_multiline_descriptions(self):
        data = copy.deepcopy(SYNTHETIC)
        data["models"] = [{"id": f"fixture/model-{index}", "description": "Line one\nLine two",
                           "efforts": ["low"], "default_effort": "low"} for index in range(250)]
        data["models"][-1]["description"] = ""
        data["models"][-1]["label"] = ""
        self.write(data)
        result = inspect_computer_models(self.path)
        self.assertEqual([row["id"] for row in result["models"]],
                         [f"fixture/model-{index}" for index in range(250)])
        self.assertEqual(result["models"][0]["description"], "Line one\nLine two")
        self.assertEqual(result["models"][-1]["description"], "")
        self.assertEqual(result["models"][-1]["label"], "")

    def test_unknown_future_labels_are_retained_but_not_accepted_as_documented_selections(self):
        data = copy.deepcopy(SYNTHETIC)
        data["modes"].append({"mode": "future-mode", "model": "fixture/model-a", "effort": "future-effort"})
        data["models"][0]["efforts"].append("future-effort")
        self.write(data)
        result = inspect_computer_models(self.path)
        self.assertEqual(result["modes"][-1]["mode"], "future-mode")
        self.assertIn("future-effort", result["models"][0]["efforts"])
        with self.assertRaises(WeaverError):
            inspect_computer_models(self.path, mode="future-mode")
        with self.assertRaises(WeaverError):
            inspect_computer_models(self.path, model="fixture/model-a", effort="future-effort")

    def test_rejects_duplicates_inconsistent_metadata_and_credentials_in_ignored_fields(self):
        cases = []
        duplicate_model = copy.deepcopy(SYNTHETIC)
        duplicate_model["models"].append(copy.deepcopy(duplicate_model["models"][0]))
        cases.append(duplicate_model)
        duplicate_mode = copy.deepcopy(SYNTHETIC)
        duplicate_mode["modes"].append(copy.deepcopy(duplicate_mode["modes"][0]))
        cases.append(duplicate_mode)
        bad_effort = copy.deepcopy(SYNTHETIC)
        bad_effort["models"][0]["default_effort"] = "high"
        cases.append(bad_effort)
        bad_fast = copy.deepcopy(SYNTHETIC)
        bad_fast["models"][0]["fast"] = 1
        cases.append(bad_fast)
        secret = copy.deepcopy(SYNTHETIC)
        secret["ignored"] = {"authorization": "do-not-output"}
        cases.append(secret)
        for item in cases:
            self.write(item)
            with self.subTest(item=item), self.assertRaises(WeaverError):
                inspect_computer_models(self.path)
        self.path.write_text('{"models": [], "models": [], "modes": []}', encoding="utf-8")
        with self.assertRaises(WeaverError):
            inspect_computer_models(self.path)

    def test_cli_is_offline_and_returns_only_validated_selection(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")):
            code, output, error = invoke("computer-models", "--input", str(self.path),
                                         "--model", "fixture/model-a", "--reasoning-effort", "low")
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["selection_fields"], {"model": "fixture/model-a", "effort": "low"})
        code, output, _ = invoke("computer-models", "--input", str(self.path), "--mode", "light",
                                 "--reasoning-effort", "low")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        code, output, _ = invoke("computer-models", "--input", str(self.path), "--mode", "light",
                                 "--model", "fixture/model-a")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")

    def test_changed_input_is_refused_instead_of_recording_an_inaccurate_hash(self):
        original = self.path.read_bytes()
        with mock.patch.object(Path, "open", side_effect=[io.BytesIO(original), io.BytesIO(original + b" ")]):
            with self.assertRaisesRegex(WeaverError, "changed while reading"):
                inspect_computer_models(self.path)

    def test_input_size_is_bounded_before_json_parsing(self):
        with mock.patch("agentweaver.computer_models._MAX_SNAPSHOT_BYTES", 32):
            with self.assertRaisesRegex(WeaverError, "exceeds 32 MiB"):
                inspect_computer_models(self.path)


if __name__ == "__main__":
    unittest.main()
