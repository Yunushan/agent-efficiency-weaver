"""Offline checks against the published workspace provider status contract."""

import json
import tempfile
import unittest
from pathlib import Path

from test_cli_exports import invoke

from agentweaver.config import WeaverError
from agentweaver.qwen_models import inspect_qwen_models


class QwenWorkspaceModelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "providers.json"
        self.row = {
            "modelId": "fixture/model(openai)", "baseModelId": "fixture/model", "name": "Fixture",
            "isCurrent": True, "isRuntime": False, "contextLimit": 100000,
            "envKey": "FIXTURE_API_KEY", "baseUrl": "https://example.invalid/v1",
            "configOptions": [{"id": "reasoning_effort", "type": "select", "currentValue": "high",
                               "options": [{"value": option} for option in ("none", "default", "high")]}],
        }
        self.response = {
            "v": 1, "workspaceCwd": "/private/fixture", "initialized": True, "acpChannelLive": False,
            "providers": [{"kind": "model_provider", "authType": "openai", "models": [self.row]}],
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.response), encoding="utf-8")

    def test_cli_retains_model_identity_and_reasoning_without_workspace_or_auth_details(self):
        code, out, err = invoke("qwen-models", "--input", str(self.path), "--auth-type", "openai",
                                "--model", "fixture/model(openai)", "--reasoning-effort", "default")
        self.assertEqual(code, 0, err)
        for omitted in ("/private/fixture", "FIXTURE_API_KEY", "example.invalid"):
            self.assertNotIn(omitted, out)
        result = json.loads(out)
        self.assertEqual(result["availability"], "unknown")
        self.assertFalse(result["inference_verified"])
        self.assertEqual(result["selection"]["model_id"], "fixture/model")
        self.assertEqual(result["selection"]["reported_reasoning"], "high")
        self.assertEqual(result["selection"]["reasoning_options"], ["none", "default", "high"])

    def test_same_model_on_two_endpoints_requires_exact_route(self):
        self.row["modelId"] = "qwen-route:v1:fixtureA"
        provider = self.response["providers"][0]
        provider["models"].append(self.row | {"modelId": "qwen-route:v1:fixtureB", "configOptions": []})
        self.save()
        with self.assertRaisesRegex(WeaverError, "exact auth type and route ID"):
            inspect_qwen_models(self.path, model="fixture/model", auth_type="openai")
        result = inspect_qwen_models(self.path, model="qwen-route:v1:fixtureA", effort="high")
        self.assertEqual(result["selection"]["route_id"], "qwen-route:v1:fixtureA")
        with self.assertRaisesRegex(WeaverError, "did not advertise"):
            inspect_qwen_models(self.path, model="qwen-route:v1:fixtureB", effort="high")

    def test_idle_and_invalid_responses_do_not_establish_model_support(self):
        for change in ({"contextLimit": True}, {"isCurrent": 1}, {"modelId": "fixture()"},
                       {"configOptions": [{"id": "reasoning_effort", "type": "select",
                                           "currentValue": "high", "options": [{"value": "low"}]}]},
                       {"raw": {"authorization": "fixture"}}):
            with self.subTest(change=change), self.assertRaises(WeaverError):
                self.response["providers"][0]["models"] = [self.row | change]
                self.save()
                inspect_qwen_models(self.path)
        self.response.update(initialized=False, providers=[])
        self.save()
        self.assertFalse(inspect_qwen_models(self.path)["initialized"])
        with self.assertRaises(WeaverError):
            inspect_qwen_models(self.path, model="fixture/model")
