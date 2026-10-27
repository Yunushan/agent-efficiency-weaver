"""Offline Kimi web snapshots describe configured aliases, not upstream entitlement."""
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

from agentweaver.config import WeaverError
from agentweaver.kimi_models import inspect_kimi_models

SYNTHETIC = {
    "code": 0, "msg": "success", "request_id": "synthetic-request",
    "data": {"items": [
        {"provider": "managed:fixture", "model": "managed fixture/model-a", "display_name": "Model A",
         "max_context_size": 8192, "capabilities": ["thinking", "image_in"],
         "support_efforts": ["low", "high"], "default_effort": "high",
         "upgrade": {"model": "managed fixture/model-b", "retirement_at": "2027-01-01T00:00:00Z",
                     "migration_markdown": "Synthetic guidance"}},
        {"provider": "local", "model": "bare-alias", "max_context_size": 4096},
    ]},
}


class KimiModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic-kimi.json"
        self.write(SYNTHETIC)

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_preserves_aliases_and_reasoning_metadata_without_exposing_request_or_paths(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")):
            result = inspect_kimi_models(self.path)
        self.assertEqual([item["alias"] for item in result["models"]], ["managed fixture/model-a", "bare-alias"])
        first = result["models"][0]
        self.assertEqual(first["provider"], "managed:fixture")
        self.assertEqual(first["max_context_size"], 8192)
        self.assertEqual(first["capabilities"], ["thinking", "image_in"])
        self.assertEqual(first["support_efforts"], ["low", "high"])
        self.assertEqual(first["default_effort"], "high")
        self.assertIs(first["default_effort_in_list"], True)
        self.assertEqual(first["upgrade"], {"model": "managed fixture/model-b", "retirement_at": "2027-01-01T00:00:00Z"})
        self.assertIsNone(result["models"][1]["support_efforts"])
        self.assertIsNone(result["models"][1]["default_effort_in_list"])
        self.assertEqual(result["selection"], {})
        self.assertEqual(result["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(result["snapshot_completeness"], "unknown")
        self.assertNotIn("synthetic-request", json.dumps(result))
        self.assertNotIn("migration_markdown", json.dumps(result))
        self.assertNotIn(str(self.path), json.dumps(result))
        self.assertFalse(result["network_used"] or result["credentials_read"] or result["inference_tested"])

    def test_explicit_effort_requires_documented_value_and_snapshot_support(self):
        result = inspect_kimi_models(self.path, model="managed fixture/model-a", effort="low")
        self.assertEqual(result["selection"], {"alias": "managed fixture/model-a", "effort": "low"})
        self.assertEqual(inspect_kimi_models(self.path, model="bare-alias")["selection"], {"alias": "bare-alias"})
        for options in ({"effort": "low"}, {"model": "missing"}, {"model": "bare-alias", "effort": "low"},
                        {"model": "managed fixture/model-a", "effort": "max"}):
            with self.subTest(options=options), self.assertRaises(WeaverError):
                inspect_kimi_models(self.path, **options)

    def test_preserves_future_effort_labels_but_does_not_validate_them_as_supported_wire_values(self):
        data = copy.deepcopy(SYNTHETIC)
        data["data"]["items"][0]["support_efforts"].append("future-effort")
        self.write(data)
        result = inspect_kimi_models(self.path)
        self.assertIn("future-effort", result["models"][0]["support_efforts"])
        with self.assertRaises(WeaverError):
            inspect_kimi_models(self.path, model="managed fixture/model-a", effort="future-effort")

    def test_inconsistent_default_is_reported_without_discarding_other_aliases(self):
        data = copy.deepcopy(SYNTHETIC)
        data["data"]["items"][0]["default_effort"] = "max"
        self.write(data)
        result = inspect_kimi_models(self.path)
        self.assertEqual(len(result["models"]), 2)
        self.assertEqual(result["models"][0]["default_effort"], "max")
        self.assertIs(result["models"][0]["default_effort_in_list"], False)
        self.assertEqual(inspect_kimi_models(self.path, model="managed fixture/model-a", effort="low")["selection"],
                         {"alias": "managed fixture/model-a", "effort": "low"})

    def test_absent_provider_or_effort_list_remains_unknown_without_dropping_alias(self):
        data = copy.deepcopy(SYNTHETIC)
        data["data"]["items"][1] = {"model": "flat-alias", "max_context_size": 4096,
                                     "default_effort": "high"}
        self.write(data)
        result = inspect_kimi_models(self.path, model="flat-alias")
        self.assertIsNone(result["models"][1]["provider"])
        self.assertEqual(result["models"][1]["default_effort"], "high")
        self.assertIsNone(result["models"][1]["default_effort_in_list"])
        with self.assertRaises(WeaverError):
            inspect_kimi_models(self.path, model="flat-alias", effort="high")

    def test_preserves_all_aliases_without_substituting_provider_model_ids(self):
        data = copy.deepcopy(SYNTHETIC)
        data["data"]["items"] = [{"provider": "fixture", "model": f"fixture/model-{i}",
                                    "max_context_size": 1024} for i in range(250)]
        self.write(data)
        result = inspect_kimi_models(self.path)
        self.assertEqual([item["alias"] for item in result["models"]],
                         [f"fixture/model-{i}" for i in range(250)])

    def test_invalid_responses_secrets_and_inconsistent_limits_fail_closed(self):
        cases = []
        for code in (1, True, "0"):
            bad = copy.deepcopy(SYNTHETIC)
            bad["code"] = code
            cases.append(bad)
        for context in (0, -1, True):
            bad = copy.deepcopy(SYNTHETIC)
            bad["data"]["items"][0]["max_context_size"] = context
            cases.append(bad)
        duplicate = copy.deepcopy(SYNTHETIC)
        duplicate["data"]["items"].append(copy.deepcopy(duplicate["data"]["items"][0]))
        cases.append(duplicate)
        secret = copy.deepcopy(SYNTHETIC)
        secret["data"]["items"][0]["unused"] = {"api_key": "do-not-output"}
        cases.append(secret)
        for item in cases:
            self.write(item)
            with self.subTest(item=item), self.assertRaises(WeaverError):
                inspect_kimi_models(self.path)
        self.path.write_text('{"code": 0, "code": 0, "data": {"items": []}}', encoding="utf-8")
        with self.assertRaises(WeaverError):
            inspect_kimi_models(self.path)

    def test_changed_and_oversize_input_are_rejected(self):
        original = self.path.read_bytes()
        with mock.patch.object(Path, "open", side_effect=[io.BytesIO(original), io.BytesIO(original + b" ")]):
            with self.assertRaisesRegex(WeaverError, "changed while reading"):
                inspect_kimi_models(self.path)
        with mock.patch("agentweaver.kimi_models._MAX_SNAPSHOT_BYTES", 32):
            with self.assertRaisesRegex(WeaverError, "exceeds 32 MiB"):
                inspect_kimi_models(self.path)

    def test_cli_checks_an_alias_offline_without_starting_or_writing_a_server(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")):
            code, output, error = invoke("kimi-models", "--input", str(self.path),
                                         "--model", "managed fixture/model-a", "--reasoning-effort", "high")
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["selection"], {"alias": "managed fixture/model-a", "effort": "high"})
        code, output, _ = invoke("kimi-models", "--input", str(self.path), "--reasoning-effort", "high")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
