"""Offline schema and handoff contracts for expanded product surfaces."""
import json
import shlex
import socket
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agentweaver.adapters import render_mcp
from agentweaver.config import WeaverError, load_json
from agentweaver.integrations import load_integrations
from agentweaver.skills import render_instruction_bundle

ROOT = Path(__file__).resolve().parents[1]
URL = "https://mcp.context7.com/mcp"


class ExpandedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        self.profile = load_json(ROOT / "profiles/efficient.json")
        self.integrations = load_integrations(ROOT)

    def render(self, identifier, surface, enabled=None):
        return render_mcp(self.integrations[identifier], surface, self.profile,
                          self.catalog, enabled or [])[0]

    def test_remote_http_dialects_remain_product_specific(self):
        cases = (
            ("factory", "cli", "factory/.factory/mcp.json", {"type": "http", "url": URL}),
            ("jetbrains-air", "desktop", "jetbrains-air/.air/mcp.json", {"url": URL}),
            ("qwen-code", "cli", "qwen-code/.qwen/settings.json", {"httpUrl": URL}),
            ("kimi-code", "cli", "kimi-code/.kimi-code/mcp.json", {"url": URL}),
        )
        for identifier, surface, path, entry in cases:
            with self.subTest(product=identifier):
                empty = self.render(identifier, surface)
                self.assertEqual(json.loads(empty.content), {"mcpServers": {}})
                enabled = self.render(identifier, surface, ["context7"])
                self.assertEqual(enabled.relative_path.as_posix(), path)
                self.assertEqual(json.loads(enabled.content), {"mcpServers": {"context7": entry}})
                self.assertEqual(enabled.kind, "mcp")
        # Qwen's `url` means legacy SSE; do not silently substitute it for httpUrl.
        qwen = json.loads(self.render("qwen-code", "cli", ["context7"]).content)
        self.assertNotIn("url", qwen["mcpServers"]["context7"])
        self.assertNotIn("trust", qwen["mcpServers"]["context7"])

    def test_user_configuration_is_staged_as_fragments_without_live_paths(self):
        for identifier, surface, filename, root in (
            ("hermes", "cli", "config.yaml", "mcp_servers"),
            ("lm-studio", "desktop", "mcp.json", "mcpServers"),
        ):
            with self.subTest(product=identifier):
                empty = self.render(identifier, surface)
                self.assertEqual(json.loads(empty.content), {root: {}})
                artifact = self.render(identifier, surface, ["context7"])
                self.assertEqual(artifact.relative_path.as_posix(), f"{identifier}/fragments/{filename}")
                self.assertEqual(artifact.kind, "mcp-fragment")
                entry = json.loads(artifact.content)[root]["context7"]
                self.assertEqual(entry["url"], URL)
                if identifier == "hermes":
                    self.assertIs(entry["enabled"], True)

    def test_api_fragments_are_empty_by_default_and_have_documented_request_shapes(self):
        cases = (
            ("perplexity", "agent-api", "tools", {
                "type": "mcp", "server_label": "context7", "server_url": URL,
                "require_approval": "always",
            }),
            ("lm-studio", "api", "integrations", {
                "type": "ephemeral_mcp", "server_label": "context7", "server_url": URL,
            }),
        )
        for identifier, surface, key, entry in cases:
            with self.subTest(product=identifier):
                empty = self.render(identifier, surface)
                self.assertEqual(json.loads(empty.content), {key: []})
                selected = self.render(identifier, surface, ["context7"])
                self.assertEqual(selected.relative_path.as_posix(), f"{identifier}/requests/{surface}.json")
                self.assertEqual(selected.kind, "mcp-request")
                self.assertEqual(json.loads(selected.content), {key: [entry]})
                self.assertNotIn("model", json.loads(selected.content))
                for secret_field in (b"authorization", b"headers", b"api_key", b"token"):
                    self.assertNotIn(secret_field, selected.content)
                self.assertNotIn(b"mcp_service_enabled", selected.content)

    def test_lmstudio_request_tools_are_scoped_per_server_and_target(self):
        spec = self.integrations["lm-studio"]
        for responses, suffix, root_key, tool_type in (
            (False, "api.json", "integrations", "ephemeral_mcp"),
            (True, "responses.json", "tools", "mcp"),
        ):
            with self.subTest(responses=responses):
                artifact, = render_mcp(spec, "api", self.profile, self.catalog, ["context7"],
                                       lmstudio_responses=responses, lmstudio_tools=["context7:resolve-library-id"])
                self.assertEqual(artifact.relative_path.as_posix(), f"lm-studio/requests/{suffix}")
                self.assertEqual(json.loads(artifact.content), {root_key: [{
                    "type": tool_type, "server_label": "context7", "server_url": URL,
                    "allowed_tools": ["resolve-library-id"],
                }]})
        empty, = render_mcp(spec, "api", self.profile, self.catalog, [], lmstudio_responses=True)
        self.assertEqual(json.loads(empty.content), {"tools": []})
        for filters in (["context7:*"], ["different:exact"], ["context7:exact", "context7:exact"]):
            with self.subTest(filters=filters), self.assertRaises(WeaverError):
                render_mcp(spec, "api", self.profile, self.catalog, ["context7"], lmstudio_tools=filters)
        profile = dict(self.profile, allowed_mcp=["context7", "openai-docs"], max_mcp_servers=2)
        with self.assertRaisesRegex(WeaverError, "for each selected"):
            render_mcp(spec, "api", profile, self.catalog, ["context7", "openai-docs"], lmstudio_tools=["context7:exact"])
        with self.assertRaisesRegex(WeaverError, "LM Studio API"):
            render_mcp(spec, "desktop", self.profile, self.catalog, ["context7"], lmstudio_responses=True)

    def test_lmstudio_native_plugin_references_can_select_stdio_without_copying_commands(self):
        profile = dict(self.profile, allowed_mcp=["context7", "local-fixture"], max_mcp_servers=2)
        catalog = {"servers": self.catalog["servers"] | {
            "local-fixture": {"kind": "stdio", "command": "python", "args": ["fixture.py"], "default_enabled": False},
        }}
        spec = self.integrations["lm-studio"]
        artifact, = render_mcp(spec, "api", profile, catalog, ["local-fixture", "context7"], lmstudio_plugins=True,
                               lmstudio_tools=["local-fixture:verify_status", "context7:query-docs"])
        self.assertEqual(artifact.relative_path.as_posix(), "lm-studio/requests/chat-plugins.json")
        self.assertEqual(json.loads(artifact.content), {"integrations": [
            {"type": "plugin", "id": "mcp/context7", "allowed_tools": ["query-docs"]},
            {"type": "plugin", "id": "mcp/local-fixture", "allowed_tools": ["verify_status"]},
        ]})
        empty, = render_mcp(spec, "api", profile, catalog, [], lmstudio_plugins=True)
        self.assertEqual(json.loads(empty.content), {"integrations": []})
        with self.assertRaisesRegex(WeaverError, "native chat request"):
            render_mcp(spec, "api", profile, catalog, ["local-fixture"], lmstudio_plugins=True, lmstudio_responses=True)
        with self.assertRaisesRegex(WeaverError, "stdio is unavailable"):
            render_mcp(spec, "api", profile, catalog, ["local-fixture"], lmstudio_responses=True)
        with self.assertRaisesRegex(WeaverError, "LM Studio API"):
            render_mcp(spec, "desktop", profile, catalog, ["context7"], lmstudio_plugins=True)

    def test_request_schema_cannot_be_project_or_user_configuration(self):
        spec = self.integrations["perplexity"]
        base = spec.surface("agent-api")
        for scope in ("project", "user", "app", "none"):
            altered = replace(spec, surfaces=(replace(base, config_scope=scope),))
            with self.subTest(scope=scope), self.assertRaises(WeaverError):
                render_mcp(altered, "agent-api", self.profile, self.catalog, [])

    def test_request_fragments_respect_profile_limits_and_perform_no_execution(self):
        enterprise = load_json(ROOT / "profiles/enterprise-secure.json")
        for identifier, surface in (("perplexity", "agent-api"), ("lm-studio", "api")):
            spec = self.integrations[identifier]
            with self.subTest(product=identifier), self.assertRaises(WeaverError):
                render_mcp(spec, surface, enterprise, self.catalog, ["context7"])
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("process used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")), \
                mock.patch.object(Path, "home", side_effect=AssertionError("ambient home inspected")):
            for identifier, surface in (("perplexity", "agent-api"), ("lm-studio", "api"),
                                        ("hermes", "cli"), ("lm-studio", "desktop")):
                self.assertTrue(self.render(identifier, surface, ["context7"]).content)

    def test_codex_local_surfaces_share_configuration_without_inventing_cloud(self):
        spec = self.integrations["codex"]
        cli = self.render("codex", "cli", ["context7"])
        for surface in ("desktop", "ide"):
            self.assertEqual(self.render("codex", surface, ["context7"]), cli)
        with self.assertRaises(WeaverError):
            spec.surface("cloud")

    def test_manual_and_provider_routes_do_not_become_native_mcp_hosts(self):
        for identifier, surface in (
            ("perplexity", "orchestrator"),
            *(("chatgpt", sid) for sid in ("desktop", "web", "work-local", "work-cloud")),
            *((identifier, spec.default_surface) for identifier, spec in self.integrations.items()
              if spec.kind == "provider"),
        ):
            with self.subTest(product=identifier, surface=surface), self.assertRaises(WeaverError):
                self.render(identifier, surface)

    def test_bionic_stages_manual_form_values_for_https_and_stdio(self):
        profile = dict(self.profile, allowed_mcp=["context7", "my-fixture"], max_mcp_servers=2)
        catalog = {"servers": self.catalog["servers"] | {
            "my-fixture": {"kind": "stdio", "command": "python", "args": ["path with spaces.py", "--read-only"],
                           "default_enabled": False},
        }}
        spec = self.integrations["lm-studio"]
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process launched")):
            artifact, = render_mcp(spec, "bionic", profile, catalog, ["my-fixture", "context7"])
            empty, = render_mcp(spec, "bionic", profile, catalog, [])
        self.assertEqual(artifact.relative_path.as_posix(), "lm-studio/mcp/bionic-setup.md")
        self.assertEqual(artifact.kind, "mcp-setup")
        blocks = artifact.content.decode().split("```json\n")[1:]
        self.assertEqual([json.loads(block.split("```")[0]) for block in blocks], [
            {"Name": "context7", "Connection": "Web address", "URL": URL},
            {"Name": "my-fixture", "Connection": "On this computer", "Command": "python",
             "Arguments": ["path with spaces.py", "--read-only"]},
        ])
        self.assertIn(b"In Settings > MCP, open Add custom MCP", artifact.content)
        self.assertIn(b"To save an entry paused, turn it off before selecting Add MCP", artifact.content)
        self.assertIn(b"Automatic is the default", artifact.content)
        self.assertIn(b"For Access token, supply the token separately", artifact.content)
        self.assertIn(b"Turn on Show advanced options, then use Add argument", artifact.content)
        self.assertIn(b"No MCP servers were selected", empty.content)
        with self.assertRaisesRegex(WeaverError, "not permitted"):
            render_mcp(spec, "bionic", self.profile, catalog, ["my-fixture"])
        catalog["servers"]["my-fixture"]["args"] = ["--read-only", ""]
        with self.assertRaisesRegex(WeaverError, "Bionic drops empty MCP arguments"):
            render_mcp(spec, "bionic", profile, catalog, ["my-fixture"])

    def test_perplexity_app_stages_only_reviewable_connector_form_values(self):
        profile = dict(self.profile, allowed_mcp=["context7", "local-test"], max_mcp_servers=2)
        argv = ["/Applications/Test Tools/node", "folder with spaces/fixture.mjs", "literal 'quote' and `backtick`"]
        catalog = {"servers": self.catalog["servers"] | {
            "local-test": {"kind": "stdio", "command": argv[0], "args": argv[1:], "default_enabled": False},
        }}
        spec = self.integrations["perplexity"]
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process launched")):
            artifact, = render_mcp(spec, "app", profile, catalog, ["local-test", "context7"])
            empty, = render_mcp(spec, "app", profile, catalog, [])
        self.assertEqual(artifact.relative_path.as_posix(), "perplexity/mcp/app-setup.md")
        self.assertEqual(artifact.kind, "mcp-setup")
        content = artifact.content.decode()
        remote, local = [json.loads(block.split("```")[0]) for block in content.split("```json\n")[1:]]
        self.assertEqual(remote, {"Name": "context7", "MCP server URL": URL})
        self.assertEqual(local["Server Name"], "local-test")
        self.assertEqual(shlex.split(local["Command"]), argv)
        self.assertIn("macOS", content)
        self.assertIn("PerplexityXPC", content)
        self.assertIn(b"No MCP servers were selected", empty.content)
        with self.assertRaisesRegex(WeaverError, "not permitted"):
            render_mcp(spec, "app", self.profile, catalog, ["local-test"])
        long_name = "s" * 65
        with self.assertRaisesRegex(WeaverError, "at most 64 characters"):
            render_mcp(spec, "app", dict(self.profile, allowed_mcp=[long_name]),
                       {"servers": {long_name: self.catalog["servers"]["context7"]}}, [long_name])

    def test_native_skill_paths_and_hermes_trust_are_explicit(self):
        for identifier, surface, instruction, skill_dir in (
            ("factory", "cli", "AGENTS.md", ".factory/skills"),
            ("jetbrains-air", "desktop", "AGENTS.md", ".agents/skills"),
            ("hermes", "cli", "AGENTS.md", ".hermes/skills"),
            ("qwen-code", "cli", "QWEN.md", ".qwen/skills"),
            ("kimi-code", "cli", "AGENTS.md", ".kimi-code/skills"),
        ):
            with self.subTest(product=identifier):
                artifacts = render_instruction_bundle(self.integrations[identifier], surface,
                                                      self.profile, ["security-review"], ROOT)
                self.assertEqual(artifacts[0].relative_path.as_posix(), f"{identifier}/{instruction}")
                self.assertEqual(artifacts[1].relative_path.as_posix(),
                                 f"{identifier}/{skill_dir}/security-review/SKILL.md")
                self.assertEqual(artifacts[1].kind, "skill")
                if identifier == "hermes":
                    text = artifacts[0].content.decode()
                    self.assertIn("hermes skills trust", text)
                    self.assertIn("does not grant project trust", text)
                    self.assertEqual(len(artifacts), 2)

    def test_chatgpt_handoffs_and_api_skills_do_not_claim_local_discovery(self):
        for identifier, surface in (
            *(("chatgpt", sid) for sid in ("desktop", "web", "work-local", "work-cloud")),
            ("perplexity", "agent-api"),
        ):
            with self.subTest(product=identifier, surface=surface):
                artifacts = render_instruction_bundle(self.integrations[identifier], surface,
                                                      self.profile, ["security-review"], ROOT)
                self.assertEqual(artifacts[0].kind, "handoff")
                self.assertEqual(artifacts[1].kind, "read-file" if identifier == "chatgpt" else "skill-import")
                self.assertFalse(any(item.kind == "skill" for item in artifacts))
                if identifier == "chatgpt":
                    self.assertIn("Hosted Work does not read local .codex", artifacts[0].content.decode())

    def test_new_documentation_evidence_does_not_claim_runtime_verification(self):
        for identifier in ("factory", "jetbrains-air", "hermes", "qwen-code", "kimi-code",
                           "chatgpt", "lm-studio", "perplexity"):
            for surface in self.integrations[identifier].surfaces:
                with self.subTest(product=identifier, surface=surface.id):
                    for capability, evidence in surface.capabilities.items():
                        self.assertFalse(evidence.runtime_checks)
                        if evidence.sources:
                            expected_date = {
                                ("jetbrains-air", "ide"): "2026-10-27",
                                ("qwen-code", "desktop"): "2026-11-13",
                                ("qwen-code", "vscode"): "2026-11-13",
                            }.get((identifier, surface.id), "2026-10-26")
                            if identifier == "jetbrains-air" and (
                                capability == "provider" or (surface.id == "ide" and capability in {"mcp", "skills"})
                            ):
                                expected_date = "2026-11-14"
                            if (identifier == "chatgpt" and capability in {"mcp", "skills"}) or (
                                identifier, surface.id, capability
                            ) in {
                                ("lm-studio", "bionic", "mcp"), ("perplexity", "app", "mcp"),
                                ("perplexity", "agent-api", "skills"), ("perplexity", "orchestrator", "mcp"),
                                ("perplexity", "orchestrator", "skills"),
                                ("lm-studio", "desktop", "skills"), ("lm-studio", "api", "skills"),
                                ("lm-studio", "desktop", "provider"),
                            }:
                                expected_date = "2026-11-15"
                            self.assertEqual(evidence.checked_on, expected_date)


if __name__ == "__main__":
    unittest.main()
