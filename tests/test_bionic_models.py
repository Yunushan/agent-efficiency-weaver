import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentweaver.bionic_models import inspect_bionic_models
from agentweaver.config import WeaverError
from tests.test_cli_exports import invoke

ROOT = Path(__file__).resolve().parents[1]
INDEXED_ID = "fixture/qwen3/sample Q4.gguf"
MAPPING = {
    "off": {"type": "native"},
    "low": {"type": "mapTo", "mapTo": "medium"},
    "medium": {"type": "native", "label": "On"},
    "high": {"type": "mapTo", "mapTo": "medium"},
    "xhigh": {"type": "mapTo", "mapTo": "medium"},
}


def _row(source, identifier, mapping=MAPPING):
    specifier = ({"type": "cloudInference", "model": identifier} if source == "cloud" else
                 {"type": "indexed", "indexedModelIdentifier": identifier})
    return {"source": source, "modelSpecifier": specifier, "displayName": "Fixture model",
            "reasoningLevelMapping": mapping, "vision": False, "trainedForToolUse": True, "quantization": "Q4"}


class BionicModelsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "picker.json"

    def save(self, rows):
        self.path.write_text(json.dumps(rows), encoding="utf-8")

    def test_bionic_preserves_native_levels_aliases_and_distinguishes_sources(self):
        self.save([_row("local", INDEXED_ID), _row("lmLink", INDEXED_ID), _row("cloud", "fixture-cloud")])
        snapshot = inspect_bionic_models(self.path)
        self.assertEqual(snapshot["model_count"], 3)
        self.assertFalse(snapshot["network_used"])
        with self.assertRaisesRegex(WeaverError, "one exact Bionic model ID"):
            inspect_bionic_models(self.path, INDEXED_ID)
        selected = inspect_bionic_models(self.path, INDEXED_ID, source="lmLink", effort="medium")["selection"]
        self.assertEqual(selected["model_specifier"], {"type": "indexed", "indexedModelIdentifier": INDEXED_ID})
        self.assertEqual(selected["reasoning_levels"], {"off": "Off", "medium": "On"})
        self.assertEqual(selected["picker_options"], selected["reasoning_levels"])
        self.assertEqual(selected["mapped_reasoning_levels"], {"low": "medium", "high": "medium", "xhigh": "medium"})
        self.assertEqual(selected["reasoning_selection"], {"level": "medium", "label": "On"})
        with self.assertRaisesRegex(WeaverError, "explicit picker option"):
            inspect_bionic_models(self.path, INDEXED_ID, source="local", effort="high")
        only_default = dict.fromkeys(MAPPING, {"type": "mapTo", "mapTo": "medium"})
        self.save([_row("local", INDEXED_ID, only_default | {"medium": {"type": "native"}})])
        with self.assertRaisesRegex(WeaverError, "explicit picker option"):
            inspect_bionic_models(self.path, INDEXED_ID, effort="medium")

    def test_bionic_picker_uses_default_menu_when_the_mapping_has_only_aliases(self):
        self.save([_row("local", INDEXED_ID, dict.fromkeys(MAPPING, {"type": "mapTo", "mapTo": "medium"}))])
        selected = inspect_bionic_models(self.path, INDEXED_ID, effort="high")["selection"]
        self.assertEqual(selected["reasoning_levels"], {})
        self.assertEqual(selected["picker_options"], {
            "off": "Off", "low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high",
        })
        self.assertEqual(selected["reasoning_selection"], {"level": "high", "label": "High"})

    def test_bionic_rejects_malformed_or_credential_bearing_picker_data(self):
        row = _row("local", INDEXED_ID)
        for invalid in (
            row | {"modelSpecifier": {"type": "cloudInference", "model": "fixture"}},
            row | {"reasoningLevelMapping": MAPPING | {"low": {"type": "mapTo", "mapTo": "max"}}},
            row | {"authorization": "synthetic"},
            row | {"vision": "false"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(WeaverError):
                self.save([invalid])
                inspect_bionic_models(self.path)
        self.save([])
        self.assertEqual(inspect_bionic_models(self.path)["catalog_completeness"], "unknown")

    def test_bionic_cli_prepares_manual_handoff_without_api_requests(self):
        self.save([_row("local", INDEXED_ID)])
        command = ("model", "--root", str(ROOT), "--agent", "lm-studio", "--surface", "bionic",
                   "--bionic-catalog", str(self.path), "--model", INDEXED_ID, "--reasoning-effort", "medium",
                   "--dry-run")
        with mock.patch("socket.socket", side_effect=AssertionError("network used")):
            code, output, error = invoke(*command)
        self.assertEqual((code, error), (0, ""))
        artifacts = json.loads(output)["artifacts"]
        selection, = (item for item in artifacts if item["kind"] == "model-selection")
        self.assertEqual(json.loads(selection["content"])["selection"]["reasoning_selection"]["label"], "On")
        self.assertFalse(any(item["kind"] == "model-config" for item in artifacts))
        code, output, error = invoke("bionic-models", "--input", str(self.path), "--model", INDEXED_ID)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["selection"]["source"], "local")
        code, _, error = invoke(*command, "--protocol", "openai-chat")
        self.assertEqual(code, 2)
        self.assertIn("picker model ID", error)
