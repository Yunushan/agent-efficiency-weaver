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
        self.assertEqual({target.surface_id for target in items["qwen-code"].targets}, {
            "cli", "desktop", "vscode",
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
        self.assertEqual(report["summary"]["surfaces_with_runtime_observations"], 26)
        self.assertEqual(report["summary"]["runtime_observation_checks"], 212)
        codex = next(item for item in report["requested_items"] if item["id"] == "codex")
        targets = {target["surface_id"]: target for target in codex["targets"]}
        versions = {"cli": "0.167.0-alpha.4.1", "desktop": "26.1106.6628.0", "ide": "1.146.0"}
        for surface, version in versions.items():
            observation, = [record for record in targets[surface]["runtime_observations"]
                            if record["interface"] == "app-server-stdio"
                            and any(check["check"] == "agent-load" for check in record["checks"])]
            self.assertEqual(observation["product_version"], version)
            self.assertEqual(observation["engine_version"], "0.167.0-alpha.4.1")
            self.assertEqual(observation["interface"], "app-server-stdio")
            self.assertEqual(observation["environment"], "isolated-signed-out")
            if surface == "ide":
                self.assertEqual(observation["extension_version"], "26.1106.51428")
                self.assertEqual(observation["os_variant"], "windows:11:10.0.26300.9711")
            else:
                self.assertNotIn("extension_version", observation)
                self.assertEqual(observation["os_variant"], "windows:11:10.0.26300")
            self.assertEqual(observation["architecture"], "x64")
            checks = {check["check"]: check for check in observation["checks"]}
            self.assertEqual({check["capability"] for check in checks.values()},
                             {"skills", "agents", "mcp", "models", "reasoning"})
            self.assertNotEqual(checks["stdio-tool-call"]["evidence_sha256"],
                                checks["stdio-subagent-approved-tool-call"]["evidence_sha256"])
            remote, = [record for record in targets[surface]["runtime_observations"]
                       if record["checks"][0]["check"] == "public-docs-https-tool-call"]
            self.assertEqual(remote["product_version"], version)
            self.assertEqual(remote["interface"], "app-server-stdio")
            self.assertEqual(remote["environment"], "isolated-signed-out")
            self.assertEqual(remote["os_variant"], "windows:11:10.0.26300.9711")
            self.assertEqual(remote["checks"][0]["capability"], "mcp")
            context7, = [record for record in targets[surface]["runtime_observations"]
                         if record["checks"][0]["check"] == "context7-https-subagent-tool-call"]
            for field in ("product_version", "engine_version", "interface", "environment", "os_variant",
                          "architecture"):
                self.assertEqual(context7[field], remote[field])
            self.assertEqual(context7.get("extension_version"), remote.get("extension_version"))
            self.assertEqual(context7["checks"][0]["capability"], "mcp")
            self.assertIn("host TLS root", context7["checks"][0]["outcome"])
            docs_subagent, = [record for record in targets[surface]["runtime_observations"]
                              if record["checks"][0]["check"] == "public-docs-https-subagent-tool-call"]
            for field in ("product_version", "engine_version", "interface", "environment", "os_variant",
                          "architecture"):
                self.assertEqual(docs_subagent[field], remote[field])
            self.assertEqual(docs_subagent.get("extension_version"), remote.get("extension_version"))
            self.assertEqual(docs_subagent["checks"][0]["capability"], "mcp")
            self.assertNotEqual(docs_subagent["checks"][0]["evidence_sha256"], remote["checks"][0]["evidence_sha256"])
            self.assertEqual(targets[surface]["capabilities"]["mcp"]["runtime_status"], "pending")
        exec_records = [record for record in targets["cli"]["runtime_observations"]
                        if record["interface"] == "exec-loopback"]
        profile, = [record for record in exec_records if record["checks"][0]["check"] == "named-profile-model-request"]
        self.assertEqual(profile["product_version"], "0.167.0-alpha.4.1")
        self.assertEqual({check["check"]: check["capability"] for check in profile["checks"]},
                         {"named-profile-model-request": "models", "named-profile-effort-precedence": "reasoning"})
        agent, = [record for record in exec_records if record["checks"][0]["check"] == "agent-load"]
        for field in ("product_version", "engine_version", "environment", "architecture"):
            self.assertEqual(agent[field], profile[field])
        self.assertEqual(agent["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual({check["check"]: check["capability"] for check in agent["checks"]},
                         {"agent-load": "agents", "stdio-subagent-tool-approval": "mcp",
                          "agent-model-effort-selection": "reasoning"})
        self.assertEqual(len(targets["desktop"]["runtime_observations"]), 4)

    def test_cursor_cli_inventory_is_bound_to_the_signed_out_windows_release(self):
        report = coverage_report(ROOT)
        cursor = next(item for item in report["requested_items"] if item["id"] == "cursor")
        targets = {row["surface_id"]: row for row in cursor["targets"]}
        self.assertEqual(set(targets), {"ide", "cli"})
        self.assertFalse(targets["ide"]["runtime_observations"])
        records = {item["interface"]: item for item in targets["cli"]["runtime_observations"]}
        self.assertEqual(set(records), {"mcp-cli-inventory", "bundled-subagent-loader", "bundled-skill-loader"})
        record = records["mcp-cli-inventory"]
        self.assertEqual(record["product_version"], "2026.11.13-eacade1")
        self.assertEqual(record["architecture"], "x64")
        self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(record["environment"], "isolated-signed-out")
        self.assertEqual({row["check"]: row["capability"] for row in record["checks"]}, {
            "cursor-cli-mcp-approval-and-tool-discovery": "mcp",
            "cursor-cli-catalog-authentication": "models",
        })
        for interface, check_id, capability in (
            ("bundled-subagent-loader", "cursor-cli-bundled-subagent-load", "agents"),
            ("bundled-skill-loader", "cursor-cli-bundled-skill-load", "skills"),
        ):
            loader = records[interface]
            self.assertEqual(loader["product_version"], record["product_version"])
            self.assertEqual(loader["os_variant"], record["os_variant"])
            self.assertEqual(loader["environment"], "isolated-native-loader")
            check, = loader["checks"]
            self.assertEqual((check["check"], check["capability"]), (check_id, capability))

    def test_openhands_observation_uses_the_released_sdk_and_wsl_variant(self):
        report = coverage_report(ROOT)
        openhands = next(item for item in report["requested_items"] if item["id"] == "openhands")
        targets = {row["surface_id"]: row for row in openhands["targets"]}
        self.assertEqual(set(targets), {"cli", "canvas"})
        self.assertEqual(resolve_product_alias(ROOT, "OpenHands Agent Canvas"), "openhands")
        self.assertEqual(targets["canvas"]["capabilities"]["mcp"]["state"], "available")
        target = targets["cli"]
        record, = target["runtime_observations"]
        self.assertEqual(target["surface_id"], "cli")
        self.assertEqual(record["product_version"], "1.16.0")
        self.assertEqual(record["engine_version"], "1.21.0")
        self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711:WSL2 Ubuntu 26.04.1")
        self.assertEqual(record["architecture"], "x64")
        self.assertEqual(record["interface"], "headless-loopback")
        self.assertEqual(record["environment"], "isolated-release-loopback")
        self.assertEqual({check["check"]: check["capability"] for check in record["checks"]}, {
            "openhands-headless-skill-invocation": "skills",
            "openhands-headless-agent-task": "agents",
            "openhands-headless-stdio-tool-call": "mcp",
            "openhands-named-profile-model-request": "models",
            "openhands-named-profile-effort-request": "reasoning",
        })

    def test_canvas_runtime_observations_are_scoped_to_the_pinned_local_backend(self):
        report = coverage_report(ROOT)
        openhands = next(item for item in report["requested_items"] if item["id"] == "openhands")
        canvas = next(row for row in openhands["targets"] if row["surface_id"] == "canvas")
        record, conversation, https = canvas["runtime_observations"]
        for observation in (record, conversation, https):
            self.assertEqual(observation["product_version"], "1.40.0")
            self.assertEqual(observation["engine_version"], "1.62.0")
            self.assertEqual(observation["interface"], "local-agent-server-asgi")
            self.assertEqual(observation["environment"], "isolated-release-loopback")
            self.assertEqual(observation["os_variant"], "windows:11:10.0.26300.9711")
            self.assertEqual(observation["architecture"], "x64")
        self.assertNotEqual(record["source_commit"], conversation["source_commit"])
        self.assertEqual({row["check"]: row["capability"] for row in record["checks"]}, {
            "canvas-disabled-mcp-settings": "mcp",
            "canvas-stdio-test-tool-call": "mcp",
            "canvas-backend-project-skill-load": "skills",
            "canvas-agent-profile-materialization": "agents",
            "canvas-llm-profile-preflight-model": "models",
            "canvas-llm-profile-preflight-effort": "reasoning",
        })
        self.assertEqual({row["check"]: row["capability"] for row in conversation["checks"]}, {
            "canvas-conversation-agent-profile": "agents",
            "canvas-conversation-skill-invocation": "skills",
            "canvas-conversation-stdio-tool-call": "mcp",
            "canvas-conversation-model-request": "models",
            "canvas-conversation-effort-request": "reasoning",
        })
        self.assertNotEqual(https["source_commit"], conversation["source_commit"])
        result, = https["checks"]
        self.assertEqual(result["check"], "canvas-conversation-context7-tool-call")
        self.assertEqual(result["capability"], "mcp")
        self.assertEqual(canvas["capabilities"]["mcp"]["runtime_status"], "pending")

    def test_zed_observation_covers_editor_mcp_startup(self):
        report = coverage_report(ROOT)
        zed = next(item for item in report["requested_items"] if item["id"] == "zed")
        target, = zed["targets"]
        record, = target["runtime_observations"]
        self.assertEqual(target["surface_id"], "ide")
        self.assertEqual(record["product_version"], "1.28.1")
        self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(record["architecture"], "x64")
        self.assertEqual(record["interface"], "editor-startup")
        self.assertEqual(record["environment"], "isolated-signed-out")
        check, = record["checks"]
        self.assertEqual((check["check"], check["capability"]), ("zed-stdio-explicit-activation", "mcp"))

    def test_hermes_cli_observation_scopes_disposable_profile_loading(self):
        report = coverage_report(ROOT)
        hermes = next(item for item in report["requested_items"] if item["id"] == "hermes")
        target, = hermes["targets"]
        self.assertEqual(target["surface_id"], "cli")
        profile, = target["runtime_observations"]
        self.assertEqual(profile["product_version"], "0.23.1")
        self.assertEqual(profile["engine_version"], "0.23.1")
        self.assertEqual(profile["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(profile["architecture"], "x64")
        self.assertEqual(profile["environment"], "isolated-signed-out")
        self.assertEqual(profile["interface"], "config-inventory")
        self.assertEqual({check["check"]: check["capability"] for check in profile["checks"]}, {
            "hermes-profile-role-loading": "agents",
            "hermes-profile-skill-view": "skills",
            "hermes-profile-stdio-tool-call": "mcp",
            "hermes-profile-model-read": "models",
            "hermes-profile-effort-resolution": "reasoning",
        })

    def test_kiro_cli_observations_distinguish_local_discovery_from_model_availability(self):
        report = coverage_report(ROOT)
        kiro = next(item for item in report["requested_items"] if item["id"] == "kiro")
        targets = {target["surface_id"]: target for target in kiro["targets"]}
        self.assertEqual(targets["ide"]["runtime_observations"], [])
        acp, inventory = targets["cli"]["runtime_observations"]
        for record in (acp, inventory):
            self.assertEqual(record["product_version"], "3.3.0")
            self.assertEqual(record["engine_version"], "3.3.0")
            self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
            self.assertEqual(record["architecture"], "x64")
            self.assertEqual(record["environment"], "isolated-signed-out")
        self.assertEqual(acp["interface"], "acp-stdio")
        self.assertEqual({check["check"]: check["capability"] for check in acp["checks"]}, {
            "kiro-v3-project-skill-discovery": "skills",
            "kiro-v3-workspace-agent-selection": "agents",
            "kiro-v3-stdio-activation": "mcp",
        })
        self.assertEqual(inventory["interface"], "config-inventory")
        self.assertEqual({check["check"]: check["capability"] for check in inventory["checks"]}, {
            "kiro-v3-model-login-required": "models",
            "kiro-v3-workspace-effort-readback": "reasoning",
        })
        self.assertEqual(targets["cli"]["capabilities"]["mcp"]["runtime_status"], "pending")

    def test_cline_ide_observations_keep_the_extension_host_scope(self):
        report = coverage_report(ROOT)
        cline = next(item for item in report["requested_items"] if item["id"] == "cline")
        targets = {target["surface_id"]: target for target in cline["targets"]}
        ide, = targets["ide"]["runtime_observations"]
        self.assertEqual(ide["interface"], "vscode-extension-host")
        self.assertEqual(ide["product_version"], "1.146.0")
        self.assertEqual(ide["extension_version"], "4.1.26")
        self.assertEqual(ide["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(ide["architecture"], "x64")
        self.assertEqual({check["check"]: check["capability"] for check in ide["checks"]}, {
            "skill-discovery": "skills",
            "cline-ide-stdio-activation": "mcp",
            "cline-ide-delegation-unavailable": "agents",
            "cline-ide-local-model-selection": "models",
            "cline-ide-reasoning-effort-request": "reasoning",
        })

    def test_lmstudio_observations_apply_to_the_headless_api(self):
        report = coverage_report(ROOT)
        studio = next(item for item in report["requested_items"] if item["id"] == "lm-studio")
        targets = {target["surface_id"]: target for target in studio["targets"]}
        observations = targets["api"]["runtime_observations"]
        self.assertEqual(len(observations), 2)
        for api in observations:
            self.assertEqual(api["product_version"], "0.0.27+1")
            self.assertEqual(api["engine_version"], "2.66.0")
            self.assertEqual(api["os_variant"], "windows:11:10.0.26300.9711")
            self.assertEqual(api["architecture"], "x64")
            self.assertEqual(api["interface"], "loopback-http")
            self.assertEqual(api["environment"], "isolated-release-loopback")
        self.assertEqual({check["check"]: check["capability"] for api in observations for check in api["checks"]}, {
            "lmstudio-local-model-inventory": "models",
            "lmstudio-local-reasoning-switch": "reasoning",
            "lmstudio-local-embedding-request": "models",
            "lmstudio-ephemeral-mcp-tool-call": "mcp",
            "lmstudio-compatible-chat-requests": "models",
            "lmstudio-decision-instance-inventory": "models",
            "lmstudio-local-decision-requests": "models",
        })
        self.assertEqual(targets["desktop"]["runtime_observations"], [])
        self.assertEqual(targets["bionic"]["runtime_observations"], [])

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

    def test_copilot_cli_observations_record_prompt_mode_separately(self):
        report = coverage_report(ROOT)
        item = next(item for item in report["requested_items"] if item["id"] == "copilot")
        target = next(target for target in item["targets"] if target["surface_id"] == "cli")
        record, = target["runtime_observations"]
        self.assertEqual(record["product_version"], "1.0.111")
        self.assertEqual(record["engine_version"], record["product_version"])
        self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(record["architecture"], "x64")
        self.assertEqual(record["interface"], "prompt-mode")
        self.assertEqual(record["environment"], "isolated-signed-out")
        self.assertNotIn("extension_version", record)
        self.assertEqual({check["check"]: check["capability"] for check in record["checks"]}, {
            "skill-discovery": "skills",
            "copilot-cli-skill-use": "skills",
            "agent-load": "agents",
            "stdio-tool-call": "mcp",
            "copilot-cli-child-mcp-filter": "mcp",
            "copilot-cli-byok-model-request": "models",
            "copilot-cli-byok-effort-request": "reasoning",
            "copilot-cli-child-model-request": "models",
            "copilot-cli-child-model-fallback": "models",
            "copilot-cli-child-effort-request": "reasoning",
        })

    def test_claude_observations_belong_to_the_bundled_executable(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "claude-code")
        targets = {target["surface_id"]: target for target in item["targets"]}
        self.assertEqual(targets["desktop"]["runtime_observations"], [])
        record, remote, loopback = targets["vscode"]["runtime_observations"]
        self.assertEqual(record["extension_version"], record["engine_version"])
        self.assertEqual(record["interface"], "bundled-runtime")
        checks = {check["check"] for check in record["checks"]}
        self.assertEqual(checks, {"skill-discovery", "agent-metadata-discovery", "project-http-pending-approval",
                                  "project-stdio-healthcheck", "signed-out-model-list", "signed-out-effort-options"})
        for field in ("product_version", "extension_version", "engine_version", "interface", "os_variant",
                      "architecture", "environment"):
            self.assertEqual(remote[field], record[field])
        [check] = remote["checks"]
        self.assertEqual(check["check"], "project-public-docs-tool-discovery")
        self.assertEqual(check["capability"], "mcp")
        self.assertEqual(loopback["environment"], "isolated-loopback")
        self.assertEqual(loopback["interface"], "bundled-runtime")
        self.assertEqual(loopback["extension_version"], record["extension_version"])
        self.assertEqual({check["check"]: check["capability"] for check in loopback["checks"]},
                         {"agent-load": "agents", "claude-child-model-request": "models",
                          "claude-child-effort-request": "reasoning", "stdio-tool-call": "mcp",
                          "claude-child-mcp-tool-filter": "mcp"})
        self.assertEqual(targets["vscode"]["capabilities"]["mcp"]["runtime_status"], "pending")

    def test_claude_standalone_observations_scope_catalog_and_tool_calls(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "claude-code")
        cli = next(target for target in item["targets"] if target["surface_id"] == "cli")
        inventory, execution = cli["runtime_observations"]
        for record in (inventory, execution):
            self.assertEqual(record["interface"], "stream-json")
            self.assertEqual(record["product_version"], "2.1.325")
            self.assertEqual(record["engine_version"], "2.1.325")
            self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
            self.assertEqual(record["architecture"], "x64")
            self.assertNotIn("extension_version", record)
        self.assertEqual(inventory["environment"], "isolated-signed-out")
        self.assertEqual(execution["environment"], "isolated-loopback")
        self.assertEqual({check["check"]: check["capability"] for check in inventory["checks"]}, {
            "skill-discovery": "skills",
            "agent-metadata-discovery": "agents",
            "project-http-pending-approval": "mcp",
            "project-stdio-healthcheck": "mcp",
            "project-public-docs-tool-discovery": "mcp",
            "signed-out-model-list": "models",
            "signed-out-effort-options": "reasoning",
        })
        self.assertEqual({check["check"]: check["capability"] for check in execution["checks"]}, {
            "agent-load": "agents",
            "claude-cli-skill-use": "skills",
            "stdio-tool-call": "mcp",
            "claude-cli-public-docs-tool-call": "mcp",
            "claude-child-mcp-tool-filter": "mcp",
            "claude-child-model-request": "models",
            "claude-child-effort-request": "reasoning",
            "claude-cli-catalog-model-requests": "models",
            "claude-cli-catalog-effort-requests": "reasoning",
            "claude-cli-default-child-model": "models",
        })

    def test_qwen_release_daemon_observations_identify_each_packaged_runtime(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "qwen-code")
        targets = {target["surface_id"]: target for target in item["targets"]}
        record, discovery, execution = targets["cli"]["runtime_observations"]
        self.assertEqual(record["product_version"], "0.27.3")
        self.assertEqual(record["interface"], "daemon-http")
        self.assertEqual(record["environment"], "isolated-release-loopback")
        self.assertEqual({check["check"]: check["capability"] for check in record["checks"]},
                         {"skill-discovery": "skills", "qwen-daemon-agent-discovery": "agents",
                          "qwen-daemon-mcp-pending-approval": "mcp", "qwen-daemon-startup-model": "models",
                          "qwen-daemon-session-effort": "reasoning"})
        self.assertEqual(discovery["interface"], "daemon-http")
        self.assertEqual(execution["interface"], "exec-loopback")
        for observation, expected in ((discovery, "qwen-daemon-approved-stdio-discovery"),
                                      (execution, "stdio-tool-call")):
            self.assertEqual(observation["environment"], "isolated-release-loopback")
            [check] = observation["checks"]
            self.assertEqual(check["check"], expected)
            self.assertEqual(check["capability"], "mcp")
        desktop, = targets["desktop"]["runtime_observations"]
        self.assertEqual(desktop["product_version"], "0.27.3")
        self.assertEqual(desktop["engine_version"], "22.20.0")
        self.assertEqual(desktop["interface"], "bundled-daemon-http")
        self.assertEqual(desktop["environment"], "isolated-release-loopback")
        self.assertEqual(desktop["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(desktop["architecture"], "x64")
        self.assertEqual({check["check"]: check["capability"] for check in desktop["checks"]}, {
            "skill-discovery": "skills",
            "qwen-daemon-skill-use": "skills",
            "qwen-daemon-agent-discovery": "agents",
            "agent-load": "agents",
            "qwen-daemon-mcp-pending-approval": "mcp",
            "qwen-daemon-approved-stdio-discovery": "mcp",
            "stdio-tool-call": "mcp",
            "qwen-daemon-startup-model": "models",
            "qwen-daemon-session-effort": "reasoning",
            "qwen-daemon-child-model-request": "models",
            "qwen-daemon-child-effort-request": "reasoning",
        })

    def test_qwen_companion_observation_names_the_extension_and_acp_runtime(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "qwen-code")
        target, = [target for target in item["targets"] if target["surface_id"] == "vscode"]
        observation, = target["runtime_observations"]
        self.assertEqual(observation["product_version"], "1.146.0")
        self.assertEqual(observation["extension_version"], "0.27.3")
        self.assertEqual(observation["engine_version"], "0.27.3")
        self.assertEqual(observation["interface"], "vscode-extension-host")
        self.assertEqual(observation["environment"], "isolated-release-loopback")
        self.assertEqual(observation["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(observation["architecture"], "x64")
        self.assertEqual({check["check"]: check["capability"] for check in observation["checks"]}, {
            "qwen-companion-skill-use": "skills", "agent-load": "agents", "qwen-companion-mcp-approval": "mcp",
            "qwen-companion-model-selection": "models", "qwen-companion-reasoning-request": "reasoning",
        })

    def test_opencode_v2_runtime_records_distinguish_config_discovery_and_execution(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "opencode")
        [target] = item["targets"]
        self.assertEqual(target["surface_id"], "cli")
        initial, active, execution, delegation = target["runtime_observations"]
        self.assertEqual(initial["product_version"], active["product_version"])
        self.assertEqual(active["product_version"], "2.3.0")
        self.assertEqual(active["interface"], "serve-http")
        self.assertEqual({check["check"]: check["capability"] for check in active["checks"]},
                         {"skill-discovery": "skills", "agent-discovery": "agents",
                          "opencode-v2-agent-variant-load": "reasoning", "opencode-v2-stdio-discovery": "mcp"})
        self.assertEqual(execution["product_version"], active["product_version"])
        self.assertEqual(execution["interface"], "exec-loopback")
        self.assertEqual(execution["environment"], "isolated-release-loopback")
        self.assertEqual({check["check"]: check["capability"] for check in execution["checks"]},
                         {"stdio-tool-call": "mcp", "opencode-v2-variant-effort-request": "reasoning"})
        self.assertEqual(delegation["interface"], "exec-loopback")
        self.assertEqual(delegation["source_commit"], "46fcb02a21948ba581efa93a82f660a7afda717f")
        self.assertEqual({check["check"]: check["capability"] for check in delegation["checks"]},
                         {"agent-load": "agents", "agent-model-effort-selection": "reasoning",
                          "opencode-v2-child-skill-use": "skills", "opencode-v2-child-mcp-filter": "mcp"})
        self.assertEqual(target["capabilities"]["mcp"]["runtime_status"], "pending")

    def test_kilo_runtime_records_keep_catalog_and_variant_loading_separate(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "kilo")
        targets = {target["surface_id"]: target for target in item["targets"]}
        discovery, execution = targets["cli"]["runtime_observations"]
        [editor] = targets["ide"]["runtime_observations"]
        for record in (discovery, execution):
            self.assertEqual(record["product_version"], "7.8.27")
            self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
            self.assertEqual(record["architecture"], "x64")
        self.assertEqual(discovery["interface"], "serve-http")
        self.assertEqual(execution["interface"], "exec-loopback")
        self.assertEqual({check["check"]: check["capability"] for check in discovery["checks"]}, {
            "skill-discovery": "skills", "agent-discovery": "agents", "kilo-stdio-discovery": "mcp",
            "kilo-model-variant-catalog": "models", "kilo-agent-variant-load": "reasoning",
        })
        self.assertEqual({check["check"]: check["capability"] for check in execution["checks"]}, {
            "stdio-tool-call": "mcp", "kilo-cli-variant-request": "reasoning", "agent-load": "agents",
            "agent-model-effort-selection": "reasoning", "kilo-child-ambient-mcp": "mcp",
        })
        self.assertEqual(editor["interface"], "vscode-extension-host")
        self.assertEqual(editor["product_version"], "1.146.0")
        self.assertEqual(editor["extension_version"], "7.8.27")
        self.assertEqual({check["check"]: check["capability"] for check in editor["checks"]}, {
            "skill-discovery": "skills", "kilo-extension-subagent-discovery": "agents",
            "kilo-stdio-discovery": "mcp", "kilo-extension-model-selection": "models",
            "kilo-extension-variant-selection": "reasoning",
        })
        self.assertEqual(targets["cli"]["capabilities"]["mcp"]["runtime_status"], "pending")

    def test_cline_runtime_records_distinguish_inventory_and_loopback_execution(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "cline")
        targets = {target["surface_id"]: target for target in item["targets"]}
        inventory, execution = targets["cli"]["runtime_observations"]
        for record in (inventory, execution):
            self.assertEqual(record["product_version"], "3.0.74")
            self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
            self.assertEqual(record["architecture"], "x64")
        self.assertEqual(inventory["interface"], "config-inventory")
        self.assertEqual(execution["interface"], "exec-loopback")
        self.assertEqual({check["check"]: check["capability"] for check in inventory["checks"]}, {
            "skill-discovery": "skills", "cline-agent-file-inventory": "agents",
            "cline-disabled-mcp-registration": "mcp",
        })
        self.assertEqual({check["check"]: check["capability"] for check in execution["checks"]}, {
            "cline-session-model-request": "models", "cline-session-effort-request": "reasoning",
            "stdio-tool-call": "mcp", "agent-load": "agents", "agent-model-effort-selection": "reasoning",
            "cline-child-skill-use": "skills", "cline-child-mcp-filter": "mcp",
        })
        self.assertEqual(targets["cli"]["capabilities"]["mcp"]["runtime_status"], "pending")
        [desktop] = targets["desktop"]["runtime_observations"]
        self.assertEqual((desktop["product_version"], desktop["engine_version"]), ("0.0.49", "0.2.0"))
        self.assertEqual(desktop["interface"], "desktop-sidecar")
        self.assertEqual(desktop["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual({check["check"]: check["capability"] for check in desktop["checks"]}, {
            "skill-discovery": "skills", "cline-agent-file-inventory": "agents",
            "cline-disabled-mcp-registration": "mcp", "cline-desktop-model-catalog": "models",
            "cline-desktop-session-model-request": "models", "cline-desktop-session-effort-request": "reasoning",
            "cline-desktop-max-effort-fallback": "reasoning", "stdio-tool-call": "mcp", "agent-load": "agents",
            "agent-model-effort-selection": "reasoning", "cline-child-skill-use": "skills",
            "cline-child-mcp-filter": "mcp",
        })

    def test_factory_runtime_records_distinguish_catalog_and_loopback(self):
        item = next(item for item in coverage_report(ROOT)["requested_items"] if item["id"] == "factory")
        [target] = item["targets"]
        workspace, catalog, custom = target["runtime_observations"]
        for record in (workspace, catalog, custom):
            self.assertEqual(record["product_version"], "1.10.1")
            self.assertEqual(record["architecture"], "x64")
            self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(catalog["interface"], "exec-jsonrpc")
        self.assertEqual(workspace["interface"], "exec-loopback")
        self.assertEqual(custom["interface"], "exec-loopback")
        def checks(record):
            return {check["check"]: check["capability"] for check in record["checks"]}

        self.assertEqual(checks(workspace), {
            "factory-droid-discovery": "agents", "factory-cli-skill-use": "skills", "stdio-tool-call": "mcp",
        })
        self.assertEqual(checks(catalog), {"factory-cli-model-catalog": "models"})
        self.assertEqual(checks(custom), {
            "factory-cli-custom-model-request": "models", "factory-cli-custom-effort-request": "reasoning",
        })

    def test_kimi_web_api_observations_cover_the_release_and_loopback_scope(self):
        report = coverage_report(ROOT)
        kimi = next(item for item in report["requested_items"] if item["id"] == "kimi-code")
        target, = kimi["targets"]
        observation, = target["runtime_observations"]
        self.assertEqual(target["surface_id"], "cli")
        self.assertEqual(observation["product_version"], "2.6.2")
        self.assertEqual(observation["engine_version"], "2.6.2")
        self.assertEqual(observation["interface"], "local-web-api")
        self.assertEqual(observation["environment"], "isolated-release-loopback")
        self.assertEqual(observation["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(observation["architecture"], "x64")
        self.assertEqual({check["check"]: check["capability"] for check in observation["checks"]}, {
            "skill-discovery": "skills",
            "kimi-web-stdio-tool-discovery": "mcp",
            "agent-load": "agents",
            "kimi-web-model-catalog": "models",
            "kimi-web-session-model-effort": "reasoning",
            "kimi-web-subagent-pool-effort": "reasoning",
        })
        self.assertEqual(target["capabilities"]["mcp"]["runtime_status"], "pending")

    def test_qoder_signed_out_inventory_limits_runtime_claims(self):
        report = coverage_report(ROOT)
        qoder = next(item for item in report["requested_items"] if item["id"] == "qoder")
        targets = {target["surface_id"]: target for target in qoder["targets"]}
        self.assertEqual(targets["ide"]["runtime_observations"], [])
        records = {row["interface"]: row for row in targets["cli"]["runtime_observations"]}
        self.assertEqual(set(records), {"acp-stdio", "config-inventory"})
        record = records["config-inventory"]
        self.assertEqual(record["product_version"], "1.1.89")
        self.assertEqual(record["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(record["architecture"], "x64")
        self.assertEqual(record["interface"], "config-inventory")
        self.assertEqual(record["environment"], "isolated-signed-out")
        self.assertEqual({check["check"]: check["capability"] for check in record["checks"]}, {
            "skill-discovery": "skills",
            "qoder-cli-stdio-healthcheck": "mcp",
            "qoder-cli-context7-https-healthcheck": "mcp",
        })
        acp = records["acp-stdio"]
        self.assertEqual(acp["product_version"], record["product_version"])
        self.assertEqual(acp["os_variant"], record["os_variant"])
        self.assertEqual(acp["architecture"], record["architecture"])
        self.assertEqual(acp["environment"], "isolated-signed-out")
        check, = acp["checks"]
        self.assertEqual((check["check"], check["capability"]), ("qoder-cli-acp-authentication", "models"))

    def test_runtime_observations_require_registered_scope_and_bounded_provenance(self):
        original = load_json(ROOT / "catalogs/requested-coverage.json")
        for field, value in (
            ("surface_id", "unknown-surface"), ("product_id", "aider"),
            ("product_version", "C:/private/client"), ("engine_version", "latest"),
            ("os_variant", "linux:22.04"), ("os_id", "unknown"), ("architecture", []),
            ("checked_on", "2026-02-31"), ("source_commit", "main"), ("interface", "desktop-ui"),
            ("interface", "bundled-runtime"), ("extension_version", "0.74.0"),
            ("environment", "signed-in"), ("environment", "isolated-loopback"),
            ("checks", []), ("private_path", "NEVER-ECHO"),
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
        scope = copy.deepcopy(original)
        ide = next(row for row in scope["runtime_observations"]
                   if (row["product_id"], row["surface_id"]) == ("codex", "ide"))
        del ide["extension_version"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_catalogs(root, scope)
            with self.assertRaisesRegex(WeaverError, "extension version must match"):
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
