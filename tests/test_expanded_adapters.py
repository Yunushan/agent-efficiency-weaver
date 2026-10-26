"""Offline schema and handoff contracts for expanded product surfaces."""
import json
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
            ("perplexity", "app"), ("perplexity", "orchestrator"),
            ("lm-studio", "bionic"),
            *(("chatgpt", sid) for sid in ("desktop", "web", "work-local", "work-cloud")),
            *((identifier, spec.default_surface) for identifier, spec in self.integrations.items()
              if spec.kind == "provider"),
        ):
            with self.subTest(product=identifier, surface=surface), self.assertRaises(WeaverError):
                self.render(identifier, surface)

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
                self.assertEqual(artifacts[1].kind, "read-file")
                self.assertFalse(any(item.kind == "skill" for item in artifacts))
                if identifier == "chatgpt":
                    self.assertIn("Hosted Work does not read local .codex", artifacts[0].content.decode())

    def test_new_documentation_evidence_does_not_claim_runtime_verification(self):
        for identifier in ("factory", "jetbrains-air", "hermes", "qwen-code", "kimi-code",
                           "chatgpt", "lm-studio", "perplexity"):
            for surface in self.integrations[identifier].surfaces:
                with self.subTest(product=identifier, surface=surface.id):
                    for evidence in surface.capabilities.values():
                        self.assertFalse(evidence.runtime_checks)
                        if evidence.sources:
                            self.assertEqual(evidence.checked_on, "2026-10-26")


if __name__ == "__main__":
    unittest.main()
