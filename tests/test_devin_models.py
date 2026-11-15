"""Synthetic Devin account catalog fixtures for offline selection."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_cli_exports import invoke

from agentweaver.config import WeaverError
from agentweaver.devin_models import inspect_devin_models, render_devin_airgap_models, render_devin_model

ROOT = Path(__file__).resolve().parents[1]


class DevinModelTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "models.json"
        self.dimension = {"label": "sidekick_message", "unit": "acus", "value": 0.125, "denominator": "message",
                          "info": "Fixture rate"}
        self.response = {
            "models": [
                {"uid": "fixture-swe-high", "name": "SWE Fixture (High)", "allowed": True,
                 "pricing_dimensions": [self.dimension]},
                {"uid": "fixture-swe-max", "name": "SWE Fixture (Max)", "allowed": False},
                {"uid": "fixture-free", "name": "SWE Fixture (Medium)", "allowed": True, "pricing_dimensions": []},
            ],
            "allowed_model_uids": [],
            "allowed_model_tags": ["tag-fixture"],
            "org_id": "private-org-fixture",
        }

    def save(self):
        self.path.write_text(json.dumps(self.response), encoding="utf-8")
        return self.path

    def test_offline_report_keeps_resolved_flags_and_captured_prices(self):
        self.response["models"][0]["pricing_dimensions"].append(
            {"label": "input", "unit": "usd", "value": 0.8, "denominator": "1M tokens"}
        )
        code, output, error = invoke("devin-models", "--input", str(self.save()), "--model", "fixture-swe-high")
        self.assertEqual(code, 0, error)
        data = json.loads(output)
        self.assertEqual(data["selection"], self.response["models"][0])
        self.assertEqual(data["allowed_model_tags"], ["tag-fixture"])
        self.assertEqual(data["allowed_model_uids"], [])
        self.assertEqual(data["snapshot_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertIsNone(data["models"][1]["pricing_dimensions"])
        self.assertEqual(data["models"][2]["pricing_dimensions"], [])
        self.assertEqual(data["availability"], "unknown")
        self.assertNotIn("private-org-fixture", output)

    def test_lookup_rejects_disallowed_uids_unknown_uids_and_picker_names(self):
        self.response["allowed_model_uids"] = ["fixture-swe-max"]
        for uid in ("fixture-swe-max", "fixture", "Fixture-swe-high", "SWE Fixture (High)"):
            with self.subTest(uid=uid), self.assertRaises(WeaverError):
                inspect_devin_models(self.save(), model=uid)

    def test_reports_unknown_list_rules_when_arrays_were_not_captured(self):
        del self.response["allowed_model_tags"]
        del self.response["allowed_model_uids"]
        report = inspect_devin_models(self.save())
        self.assertIsNone(report["allowed_model_tags"])
        self.assertIsNone(report["allowed_model_uids"])
        self.assertIsNone(report["selection"])

    def test_model_uid_remains_inspectable_when_picker_label_is_ambiguous(self):
        self.response["models"][-1]["name"] = self.response["models"][0]["name"]
        path = self.save()
        self.assertEqual(inspect_devin_models(path, model="fixture-swe-high")["selection"]["uid"], "fixture-swe-high")
        with self.assertRaisesRegex(WeaverError, "ambiguous picker label"):
            render_devin_model(path, "fixture-swe-high")

    def test_rejects_ambiguous_eligibility_pricing_and_duplicates(self):
        model = self.response["models"][0]
        candidates = [
            {**model, "allowed": "true"},
            {**model, "pricing_dimensions": [{**self.dimension, "value": True}]},
            {**model, "pricing_dimensions": [{**self.dimension, "value": -1}]},
            {**model, "pricing_dimensions": [{**self.dimension, "value": float("inf")}]},
            {**model, "pricing_dimensions": [{**self.dimension, "denominator": None}]},
            dict(self.response["models"][1]),
        ]
        for entry in candidates:
            self.response["models"][0] = entry
            with self.subTest(entry=entry), self.assertRaises(WeaverError):
                inspect_devin_models(self.save())

    def test_credentials_are_rejected_even_in_ignored_fields(self):
        for value in ({"api_key": "fixture"}, "cog_syntheticKeyDontEcho"):
            self.response["unrelated"] = value
            code, output, error = invoke("devin-models", "--input", str(self.save()))
            self.assertEqual(code, 2)
            self.assertEqual(output, "")
            self.assertIn("metadata", error)
            self.assertNotIn("syntheticKey", error)

    def test_session_stage_preserves_exact_label_and_validates_route(self):
        arguments = ("--root", str(ROOT), "--devin-catalog", str(self.save()), "--dry-run")
        command = ("model", "--agent", "devin", "--surface", "desktop-local", *arguments)
        code, output, error = invoke(*command, "--model", "fixture-swe-high")
        self.assertEqual(code, 0, error)
        artifacts = {row["path"]: row for row in json.loads(output)["artifacts"]}
        selected = artifacts["devin/model/model-selection.json"]
        self.assertEqual(selected["kind"], "model-selection")
        self.assertEqual(json.loads(selected["content"])["picker_name"], "SWE Fixture (High)")
        self.assertIn("displayed reasoning variant", artifacts["devin/model/model-setup.md"]["content"])
        self.assertTrue(all(path.startswith("devin/") and "/.devin/" not in path for path in artifacts))
        for extra in (("--model", "fixture-swe-max"),
                      ("--model", "fixture-swe-high", "--reasoning-effort", "high"),
                      ("--model", "fixture-swe-high", "--provider", "openrouter")):
            with self.subTest(extra=extra):
                self.assertEqual(invoke(*command, *extra)[0], 2)
        self.assertEqual(invoke("model", "--agent", "codex", "--surface", "cli", *arguments,
                                "--model", "fixture-swe-high")[0], 2)


class DevinAirgapModelTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.input = self.directory / "input.json"
        self.model = {
            "name": "local-reasoner:small",
            "display_name": "Local Reasoner",
            "base_url": "https://gateway.example.test/openai/deployments/local?api-version=2024-02-01",
            "api_key": {"env": "DEVIN_TEST_KEY", "header": "api-key"},
            "headers": {"X-Tenant": "test-team"},
            "api": "responses",
            "capabilities": ["reasoning", "vision", "parallel_tool_calls"],
            "max_context_tokens": 8192,
            "max_output_tokens": 1024,
            "default": True,
        }
        self.minimal = {"name": "chat", "base_url": "http://[::1]:11434/v1",
                        "api_key": {"env": "DEVIN_LOCAL_KEY"}, "max_context_tokens": 4096}

    def save(self, models=None, **fields):
        document = {"schema_version": 0, "models": [self.model, self.minimal] if models is None else models}
        document.update(fields)
        self.input.write_text(json.dumps(document), encoding="utf-8")
        return document

    def test_command_stages_native_schema_and_preserves_implicit_defaults(self):
        document = self.save()
        output_dir = self.directory / "out"
        args = ("devin-airgap-models", "--input", str(self.input), "--output-dir", str(output_dir))
        code, output, errors = invoke(*args, "--dry-run")
        self.assertEqual(code, 0, errors)
        self.assertFalse(output_dir.exists())
        config, handoff = json.loads(output)["artifacts"]
        self.assertEqual(config["path"], "devin/fragments/airgap/models.json")
        self.assertEqual(json.loads(config["content"]), document)
        self.assertIn("separately licensed air-gapped", handoff["content"])
        self.assertIn("DEVIN_MODELS_FILE", handoff["content"])
        code, _, errors = invoke(*args)
        self.assertEqual(code, 0, errors)
        self.assertEqual((output_dir / config["path"]).read_text(encoding="utf-8"), config["content"])
        self.assertEqual(json.loads(self.input.read_bytes()), document)

    def test_validation_rejects_unknown_fields_duplicates_and_type_mismatches(self):
        for models, fields in (
            ([], {}),
            ([self.model], {"schema_version": False}),
            ([self.model], {"endpoint": self.model["base_url"]}),
            ([self.model, self.model], {}),
            ([self.model, {**self.minimal, "default": True}], {}),
            ([{**self.model, "default": "false"}], {}),
            ([{**self.model, "max_context_tokens": True}], {}),
            ([{**self.model, "max_output_tokens": 0}], {}),
            ([{**self.model, "max_output_tokens": 8193}], {}),
            ([{**self.model, "reasoning_effort": "high"}], {}),
            ([{**self.model, "capabilities": ["reasoning", "reasoning"]}], {}),
            ([{**self.model, "api": "chat"}], {}),
        ):
            with self.subTest(models=models, fields=fields), self.assertRaises(WeaverError):
                self.save(models, **fields)
                render_devin_airgap_models(self.input)

    def test_accepts_documented_loopback_addresses(self):
        for host in ("localhost", "127.0.0.8", "[::1]"):
            with self.subTest(host=host):
                document = self.save([{**self.minimal, "base_url": f"http://{host}:11434/v1"}])
                config, _ = render_devin_airgap_models(self.input)
                self.assertEqual(json.loads(config.content), document)

    def test_rejects_inline_credentials_conflicting_headers_and_unsafe_urls(self):
        for change in (
            {"api_key": "fixture"},
            {"api_key": {"env": "DEVIN_TEST_KEY", "key": "fixture"}},
            {"api_key": {"env": "not an env var"}},
            {"headers": {"API-Key": "fixture"}},
            {"headers": {"Proxy-Authorization": "fixture"}},
            {"headers": {"x-tenant": "a", "X-Tenant": "b"}},
            {"headers": {"bad:header": "a"}},
            {"headers": {"x-tenant": "cog_syntheticKeyDontEcho"}},
            {"base_url": "http://model-gateway.example.test/v1"},
            {"base_url": "https://name:syntheticKey@localhost/v1"},
            {"base_url": "https://localhost/v1?token=syntheticKey"},
            {"base_url": "https://localhost:abc/v1"},
        ):
            with self.subTest(change=change):
                self.save([{**self.model, **change}])
                code, output, error = invoke("devin-airgap-models", "--input", str(self.input), "--dry-run")
                self.assertEqual((code, output), (2, ""))
                self.assertNotIn("syntheticKey", error)
