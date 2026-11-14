"""Offline checks against the published workspace provider status contract."""

import json
import tempfile
import unittest
from pathlib import Path

from test_cli_exports import ROOT, invoke

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

    def test_starts_with_the_exact_snapshot_route_and_omits_unrequested_effort(self):
        self.row["modelId"] = "qwen-route:v1:fixtureA"
        self.response["providers"][0]["models"].append(self.row | {"modelId": "qwen-route:v1:fixtureB"})
        self.save()
        for surface in ("desktop", "vscode"):
            with self.subTest(surface=surface):
                self._check_startup_route(surface)

    def _check_startup_route(self, surface):
        base = ("--root", str(ROOT), "--agent", "qwen-code", "--surface", surface,
                "--qwen-workspace-catalog", str(self.path), "--qwen-auth-type", "openai", "--dry-run")
        for effort in (None, "none", "default", "high"):
            with self.subTest(effort=effort):
                extra = () if effort is None else ("--reasoning-effort", effort)
                code, out, err = invoke("model", *base, "--model", "qwen-route:v1:fixtureB", *extra)
                self.assertEqual(code, 0, err)
                self.assertNotIn("/private/fixture", out)
                item = next(entry for entry in json.loads(out)["artifacts"] if entry["kind"] == "session-startup")
                config = {"modelServiceId": "qwen-route:v1:fixtureB"}
                if effort is not None:
                    config["reasoningEffort"] = effort
                self.assertEqual(json.loads(item["content"]), {"sessionScope": "thread", "startupConfig": config})
        self.assertEqual(invoke("model", *base, "--model", "fixture/model")[0], 2)
        for extra in (("--reasoning-effort", "low"), ("--provider", "openai"), ("--context-size", "100000")):
            self.assertEqual(invoke("model", *base, "--model", "qwen-route:v1:fixtureB", *extra)[0], 2)
        self.assertEqual(invoke("model", *base, "--model", "qwen-route:v1:fixtureB", "--agent", "codex",
                                "--surface", "cli")[0], 2)
