import copy
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from agentweaver.config import WeaverError, load_json
from agentweaver.coverage import coverage_report, load_requested_scope, resolve_product_alias
from agentweaver.integrations import CAPABILITY_NAMES, load_integrations

ROOT = Path(__file__).resolve().parents[1]
REQUESTED_IDS = {
    "codex", "chatgpt", "kilo", "opencode", "vscode-agents", "cline", "factory", "devin",
    "jetbrains-air", "copilot", "cursor", "openhands", "zed", "claude-code", "kiro", "antigravity",
    "hermes", "qwen-code", "qoder", "kimi-code", "openrouter", "perplexity", "lm-studio",
}


class RequestedCoverageTests(unittest.TestCase):
    def test_every_requested_product_or_group_has_explicit_registry_targets(self):
        scope = load_requested_scope(ROOT)
        self.assertEqual({item.id for item in scope}, REQUESTED_IDS)
        integrations = load_integrations(ROOT)
        for item in scope:
            self.assertTrue(item.aliases)
            self.assertTrue(item.targets)
            for target in item.targets:
                self.assertIn(target.product_id, integrations)
                self.assertIn(target.surface_id, integrations[target.product_id].surface_ids())

    def test_explicit_user_spellings_resolve_without_vendor_inference(self):
        aliases = {
            "OpenAIcodex": "codex", "kiloAI": "kilo", "FactoryDroid": "factory",
            "DevinDesktop": "devin", "JetBrainsAir": "jetbrains-air", "GitHubCopilot": "copilot",
            "ZedAI": "zed", "ClaudeCode": "claude-code", "GoogleAntigravity": "antigravity",
            "QwenCode": "qwen-code", "KimiCode": "kimi-code", "LMStudio": "lm-studio",
            "ChatGPT/Work": "chatgpt", "ChatGPT Work": "chatgpt",
        }
        for alias, expected in aliases.items():
            self.assertEqual(resolve_product_alias(ROOT, alias), expected)
            self.assertEqual(resolve_product_alias(ROOT, alias.upper()), expected)
        self.assertEqual(resolve_product_alias(ROOT, "junie"), "junie")
        self.assertNotEqual(resolve_product_alias(ROOT, "JetBrainsAir"), "junie")

    def test_vscode_group_names_only_explicit_extension_surfaces(self):
        item = next(item for item in load_requested_scope(ROOT) if item.id == "vscode-agents")
        self.assertEqual({(target.product_id, target.surface_id) for target in item.targets}, {
            ("copilot", "vscode"), ("cline", "ide"), ("kilo", "ide"), ("codex", "ide"),
            ("claude-code", "vscode"),
        })
        with self.assertRaisesRegex(WeaverError, "multiple products"):
            resolve_product_alias(ROOT, "VSCodeagents")

    def test_distinct_app_api_and_work_surfaces_are_not_collapsed(self):
        items = {item.id: item for item in load_requested_scope(ROOT)}
        self.assertEqual({target.surface_id for target in items["chatgpt"].targets}, {
            "desktop", "web", "work-local", "work-cloud",
        })
        self.assertEqual({target.surface_id for target in items["perplexity"].targets}, {
            "app", "agent-api", "orchestrator",
        })
        self.assertEqual({target.surface_id for target in items["lm-studio"].targets}, {
            "desktop", "bionic", "api",
        })

    def test_reviewed_documentation_does_not_erase_unknown_support_or_pending_runtime(self):
        report = coverage_report(ROOT)
        self.assertEqual(report["summary"]["mapped_items"], len(REQUESTED_IDS))
        self.assertGreater(report["summary"]["capability_states"]["unknown"], 0)
        self.assertEqual(report["summary"]["missing_documentation_capabilities"], 0)
        rows = [target for item in report["requested_items"] for target in item["targets"]]
        unique = {(row["product_id"], row["surface_id"]) for row in rows}
        self.assertEqual(report["summary"]["referenced_surfaces"], len(unique))
        self.assertEqual(report["summary"]["pending_runtime_capabilities"], len(unique) * len(CAPABILITY_NAMES))
        for row in rows:
            self.assertEqual(set(row["capabilities"]), set(CAPABILITY_NAMES))
            for evidence in row["capabilities"].values():
                self.assertTrue(evidence["sources"])
                self.assertIsNotNone(evidence["checked_on"])
                self.assertEqual(evidence["runtime_status"], "pending")
                self.assertEqual(evidence["runtime_checks"], [])
                if evidence["state"] == "unknown" and not evidence["sources"]:
                    self.assertEqual(evidence["documentation_status"], "missing")
        text = json.dumps(report)
        self.assertNotIn("support_percentage", text)
        self.assertNotIn(str(ROOT), text)
        self.assertIn("does not establish support", " ".join(report["limitations"]))

    def test_runtime_references_are_reported_separately_from_documentation(self):
        registry = load_json(ROOT / "catalogs/integrations.json")
        scope = load_json(ROOT / "catalogs/requested-coverage.json")
        codex = next(item for item in registry["integrations"] if item["id"] == "codex")
        codex["surfaces"][0]["capabilities"]["mcp"]["runtime_checks"] = ["evidence/example.json"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_catalogs(root, scope, registry)
            report = coverage_report(root)
        row = next(item for item in report["requested_items"] if item["id"] == "codex")["targets"][0]
        evidence = row["capabilities"]["mcp"]
        self.assertEqual(evidence["runtime_status"], "recorded")
        self.assertEqual(evidence["runtime_checks"], ["evidence/example.json"])
        self.assertEqual(evidence["documentation_status"], "recorded")
        self.assertNotIn("verified", evidence)

    def test_codex_observations_keep_interface_platform_and_check_scope(self):
        report = coverage_report(ROOT)
        self.assertEqual(report["summary"]["surfaces_with_runtime_observations"], 4)
        self.assertEqual(report["summary"]["runtime_observation_checks"], 28)
        codex = next(item for item in report["requested_items"] if item["id"] == "codex")
        targets = {target["surface_id"]: target for target in codex["targets"]}
        self.assertEqual(targets["ide"]["runtime_observations"], [])
        for surface, version in (("cli", "0.167.0-alpha.4.1"), ("desktop", "26.1106.6628.0")):
            observation, = [record for record in targets[surface]["runtime_observations"]
                            if record["interface"] == "app-server-stdio"]
            self.assertEqual(observation["product_version"], version)
            self.assertEqual(observation["engine_version"], "0.167.0-alpha.4.1")
            self.assertEqual(observation["interface"], "app-server-stdio")
            self.assertEqual(observation["environment"], "isolated-signed-out")
            self.assertEqual(observation["os_variant"], "windows:11:10.0.26300")
            self.assertEqual(observation["architecture"], "x64")
            checks = {check["check"]: check for check in observation["checks"]}
            self.assertEqual({check["capability"] for check in checks.values()},
                             {"skills", "agents", "mcp", "models", "reasoning"})
            self.assertNotEqual(checks["stdio-tool-call"]["evidence_sha256"],
                                checks["stdio-subagent-approved-tool-call"]["evidence_sha256"])
            self.assertEqual(targets[surface]["capabilities"]["mcp"]["runtime_status"], "pending")
        profile, = [record for record in targets["cli"]["runtime_observations"]
                    if record["interface"] == "exec-loopback"]
        self.assertEqual(profile["product_version"], "0.167.0-alpha.4.1")
        self.assertEqual({check["check"]: check["capability"] for check in profile["checks"]},
                         {"named-profile-model-request": "models", "named-profile-effort-precedence": "reasoning"})
        self.assertEqual(len(targets["desktop"]["runtime_observations"]), 1)

    def test_copilot_observations_name_the_bundled_runtime_and_bound_discovery(self):
        report = coverage_report(ROOT)
        item = next(item for item in report["requested_items"] if item["id"] == "copilot")
        self.assertEqual({target["surface_id"] for target in item["targets"]}, {"cli", "desktop", "vscode"})
        desktop = next(target for target in item["targets"] if target["surface_id"] == "desktop")
        self.assertEqual(desktop["runtime_observations"], [])
        target = next(target for target in item["targets"] if target["surface_id"] == "vscode")
        observation, tool_call, model_load = target["runtime_observations"]
        self.assertEqual(observation["product_version"], "1.146.0")
        self.assertEqual(observation["extension_version"], "0.74.0")
        self.assertEqual(observation["engine_version"], "1.0.73")
        self.assertEqual(observation["interface"], "bundled-runtime")
        self.assertEqual({check["check"] for check in observation["checks"]},
                         {"agent-discovery", "skill-discovery", "workspace-mcp-parse"})
        self.assertEqual(tool_call["interface"], "bundled-runtime")
        self.assertEqual(tool_call["checks"][0]["check"], "workspace-stdio-tool-call")
        self.assertEqual(tool_call["checks"][0]["capability"], "mcp")
        self.assertEqual([check["check"] for check in model_load["checks"]],
                         ["agent-model-effort-config-load", "agent-model-array-rejected"])
        self.assertEqual(target["capabilities"]["mcp"]["runtime_status"], "pending")

    def test_claude_observations_belong_to_the_bundled_executable(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "claude-code")
        targets = {target["surface_id"]: target for target in item["targets"]}
        self.assertEqual(targets["cli"]["runtime_observations"], [])
        [record] = targets["vscode"]["runtime_observations"]
        self.assertEqual(record["extension_version"], record["engine_version"])
        self.assertEqual(record["interface"], "bundled-runtime")
        checks = {check["check"] for check in record["checks"]}
        self.assertEqual(checks, {"skill-discovery", "agent-metadata-discovery", "project-http-pending-approval",
                                  "project-stdio-healthcheck", "signed-out-model-list", "signed-out-effort-options"})

    def test_runtime_observations_require_registered_scope_and_bounded_provenance(self):
        original = load_json(ROOT / "catalogs/requested-coverage.json")
        for field, value in (
            ("surface_id", "unknown-surface"), ("product_id", "aider"),
            ("product_version", "C:/private/client"), ("engine_version", "latest"),
            ("os_variant", "linux:22.04"), ("os_id", "unknown"), ("architecture", []),
            ("checked_on", "2026-02-31"), ("source_commit", "main"), ("interface", "desktop-ui"),
            ("interface", "bundled-runtime"), ("extension_version", "0.74.0"),
            ("environment", "signed-in"), ("checks", []), ("private_path", "NEVER-ECHO"),
            ("checks", [{"check": "successful-inference", "evidence_sha256": "a" * 64}]),
            ("checks", [{"check": "agent-load", "evidence_sha256": "NEVER-ECHO"}]),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                scope = copy.deepcopy(original)
                scope["runtime_observations"][0][field] = value
                self._write_catalogs(root, scope)
                with self.assertRaises(WeaverError) as caught:
                    coverage_report(root)
                self.assertNotIn("NEVER-ECHO", str(caught.exception))
        scope = copy.deepcopy(original)
        duplicated = dict(scope["runtime_observations"][0]["checks"][0], evidence_sha256="b" * 64)
        scope["runtime_observations"][0]["checks"].append(duplicated)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_catalogs(root, scope)
            with self.assertRaisesRegex(WeaverError, "Duplicate runtime observation check"):
                coverage_report(root)

    def test_scope_records_are_frozen(self):
        item = load_requested_scope(ROOT)[0]
        with self.assertRaises(FrozenInstanceError):
            item.label = "changed"
        with self.assertRaises(FrozenInstanceError):
            item.targets[0].product_id = "changed"

    def test_unknown_or_malformed_aliases_fail_without_echoing_values(self):
        for value in ("unknown-product", "missing-NEVER-ECHO", " codex", "codex ",
                      "codex\nNEVER-ECHO", "codex\x00NEVER-ECHO", None, [], {}):
            with self.subTest(value=repr(value)), self.assertRaises(WeaverError) as caught:
                resolve_product_alias(ROOT, value)
            self.assertNotIn("NEVER-ECHO", str(caught.exception))

    def test_catalog_rejects_alias_collisions_and_canonical_shadowing(self):
        original = load_json(ROOT / "catalogs/requested-coverage.json")
        for alias in ("CODEX", "aider"):
            scope = copy.deepcopy(original)
            scope["requested_items"][1]["aliases"].append(alias)
            self._assert_invalid(scope)
        scope = copy.deepcopy(original)
        scope["requested_items"][0]["aliases"].append("CODEX")
        self._assert_invalid(scope)

    def test_catalog_rejects_unknown_products_surfaces_and_duplicate_targets(self):
        original = load_json(ROOT / "catalogs/requested-coverage.json")
        for field, value in (("product_id", "unknown-product"), ("surface_id", "unknown-surface"),
                             ("product_id", "../NEVER-ECHO"), ("surface_id", "CON")):
            scope = copy.deepcopy(original)
            scope["requested_items"][0]["targets"][0][field] = value
            self._assert_invalid(scope)
        scope = copy.deepcopy(original)
        scope["requested_items"][0]["targets"].append(scope["requested_items"][0]["targets"][0])
        self._assert_invalid(scope)

    def test_catalog_is_closed_and_requires_explicit_nonempty_lists(self):
        original = load_json(ROOT / "catalogs/requested-coverage.json")
        for key, value in (("schema_version", 2), ("schema_version", True), ("requested_items", []),
                           ("requested_items", {}), ("NEVER-ECHO", "secret")):
            scope = copy.deepcopy(original)
            scope[key] = value
            self._assert_invalid(scope)
        for key, value in (("aliases", []), ("targets", []), ("aliases", "codex"),
                           ("aliases", [" codex"]), ("id", "BadId"), ("label", "bad\nNEVER-ECHO"),
                           ("NEVER-ECHO", True)):
            scope = copy.deepcopy(original)
            scope["requested_items"][0][key] = value
            self._assert_invalid(scope)
        scope = copy.deepcopy(original)
        scope["requested_items"].append(copy.deepcopy(scope["requested_items"][0]))
        self._assert_invalid(scope)

    def test_report_and_resolution_do_not_read_credentials_or_access_network(self):
        with mock.patch("os.getenv", side_effect=AssertionError("environment read")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("network")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process")):
            self.assertEqual(resolve_product_alias(ROOT, "LMStudio"), "lm-studio")
            self.assertEqual(coverage_report(ROOT)["schema_version"], 1)

    def _write_catalogs(self, root, scope, registry=None):
        (root / "catalogs").mkdir()
        if registry is None:
            registry = load_json(ROOT / "catalogs/integrations.json")
        for name, data in (("integrations", registry), ("requested-coverage", scope)):
            (root / f"catalogs/{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def _assert_invalid(self, scope):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_catalogs(root, scope)
            with self.assertRaises(WeaverError) as caught:
                load_requested_scope(root)
            self.assertNotIn("NEVER-ECHO", str(caught.exception))
            self.assertNotIn(str(root), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
