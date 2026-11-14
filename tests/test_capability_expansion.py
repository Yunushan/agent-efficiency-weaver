"""Offline fixtures for first-party IDE schemas and manual Skill import boundaries."""
import json
import os
import socket
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agentweaver.adapters import render_mcp, stdio_inventory
from agentweaver.artifacts import validate_artifacts, write_artifacts
from agentweaver.config import WeaverError, load_json
from agentweaver.integrations import get_integration
from agentweaver.skills import render_instruction_bundle, validate_skills

ROOT = Path(__file__).resolve().parents[1]
SKILL = "security-review"


class CapabilityExpansionTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_json(ROOT / "profiles/efficient.json")
        self.catalog = load_json(ROOT / "catalogs/mcp-servers.json")

    def bundle(self, product, surface, skills=None):
        return render_instruction_bundle(get_integration(product, ROOT), surface, self.profile,
                                         [SKILL] if skills is None else skills, ROOT)

    def test_new_native_skills_keep_canonical_bytes_and_documented_project_paths(self):
        canonical = (ROOT / ".agents/skills" / SKILL / "SKILL.md").read_bytes().replace(b"\r\n", b"\n")
        for product, path in (("cline", ".cline/skills"), ("cursor", ".cursor/skills"),
                              ("qoder", ".qoder/skills")):
            with self.subTest(product=product):
                artifacts = self.bundle(product, "ide")
                self.assertEqual(artifacts[0].relative_path.as_posix(), f"{product}/AGENTS.md")
                self.assertEqual(artifacts[1].relative_path.as_posix(), f"{product}/{path}/{SKILL}/SKILL.md")
                self.assertEqual(artifacts[1].kind, "skill")
                self.assertEqual(artifacts[1].content, canonical)
                self.assertNotIn(b"manual read-file projection", artifacts[0].content)

    def test_qoder_ide_mcp_is_an_empty_default_manual_ui_fragment(self):
        spec = get_integration("qoder", ROOT)
        empty = render_mcp(spec, "ide", self.profile, self.catalog, [])[0]
        self.assertEqual(empty.relative_path.as_posix(), "qoder/fragments/mcp.ide.json")
        self.assertEqual(empty.kind, "mcp-fragment")
        self.assertEqual(json.loads(empty.content), {"mcpServers": {}})
        enabled = render_mcp(spec, "ide", self.profile, self.catalog, ["context7"])[0]
        self.assertEqual(json.loads(enabled.content), {"mcpServers": {
            "context7": {"type": "sse", "url": "https://mcp.context7.com/mcp"}}})
        self.assertEqual(spec.surface("ide").capabilities["mcp"].state, "manual")
        self.assertIsNone(spec.surface("ide").native_path_hint)
        cli = render_mcp(spec, "cli", self.profile, self.catalog, ["context7"])[0]
        self.assertEqual(json.loads(cli.content)["mcpServers"]["context7"]["type"], "http")

    def test_qoder_ide_stdio_uses_separate_documented_shape_and_profile_opt_in(self):
        self.catalog["servers"]["synthetic-stdio"] = {
            "kind": "stdio", "command": "synthetic-fixture", "args": ["--fixture-only"],
            "default_enabled": False, "description": "Synthetic fixture; never executed.",
        }
        spec = get_integration("qoder", ROOT)
        with self.assertRaises(WeaverError):
            render_mcp(spec, "ide", self.profile, self.catalog, ["synthetic-stdio"])
        profile = dict(self.profile, allowed_mcp=["synthetic-stdio"])
        artifact = render_mcp(spec, "ide", profile, self.catalog, ["synthetic-stdio"])[0]
        self.assertEqual(json.loads(artifact.content), {"mcpServers": {
            "synthetic-stdio": {"command": "synthetic-fixture", "args": ["--fixture-only"]}}})
        extra = stdio_inventory()["qoder"]["additional_routes"]
        self.assertEqual(extra[0]["mcp_dialect"], "mcpServers.type-sse")
        self.assertIn("https://docs.qoder.com/user-guide/chat/model-context-protocol", extra[0]["sources"])

    def test_app_scope_requires_product_surface_and_dialect_match(self):
        original = get_integration("qoder", ROOT)
        for product, surface, dialect in (("cursor", "ide", "mcpServers.type-sse"),
                                          ("qoder", "cli", "mcpServers.type-sse"),
                                          ("qoder", "ide", "mcpServers.type-http")):
            with self.subTest(product=product, surface=surface, dialect=dialect):
                altered = replace(original.surface("ide"), id=surface, mcp_dialect=dialect)
                spec = replace(original, id=product, surfaces=(altered,))
                with self.assertRaises(WeaverError):
                    render_mcp(spec, surface, self.profile, self.catalog, [])

    def test_manual_vendor_import_preserves_skill_filename_without_claiming_discovery(self):
        for product, surface, ui in (("lm-studio", "bionic", "Settings > Skills"),
                                     ("perplexity", "app", "Plugins > Add custom > Skill > Upload skill")):
            with self.subTest(product=product):
                artifacts = self.bundle(product, surface)
                self.assertEqual(artifacts[0].kind, "handoff")
                self.assertEqual(artifacts[1].kind, "skill-import")
                self.assertEqual(artifacts[1].relative_path.as_posix(),
                                 f"{product}/imports/skills/{SKILL}/SKILL.md")
                self.assertEqual(artifacts[1].content,
                                 (ROOT / ".agents/skills" / SKILL / "SKILL.md").read_bytes().replace(b"\r\n", b"\n"))
                text = artifacts[0].content.decode()
                self.assertIn(ui, text)
                self.assertIn("not a native discovery path", text)
                self.assertFalse(any(artifact.kind == "skill" for artifact in artifacts))
                self.assertEqual(len(self.bundle(product, surface, [])), 1)

    def test_model_gateway_and_classic_surfaces_keep_native_skill_gaps_explicit(self):
        for product, surface in (("perplexity", "orchestrator"), ("lm-studio", "desktop"),
                                 ("lm-studio", "api")):
            with self.subTest(product=product, surface=surface):
                spec = get_integration(product, ROOT)
                self.assertEqual(spec.surface(surface).capabilities["skills"].state, "unknown")
                artifacts = self.bundle(product, surface)
                self.assertEqual([item.kind for item in artifacts], ["handoff", "read-file"])
                self.assertNotIn("imports/skills", artifacts[1].relative_path.as_posix())
        text = self.bundle("perplexity", "orchestrator")[0].content.decode()
        self.assertIn("do not register API Skills", text)
        self.assertIn("Bionic is a separate app", self.bundle("lm-studio", "desktop")[0].content.decode())

    def test_unestablished_mcp_schemas_do_not_inherit_other_surfaces(self):
        for product, surface in (("perplexity", "orchestrator"), ("lm-studio", "bionic")):
            spec = get_integration(product, ROOT)
            with self.subTest(product=product), self.assertRaises(WeaverError):
                render_mcp(spec, surface, self.profile, self.catalog, [])
            self.assertIsNone(spec.surface(surface).mcp_dialect)

    def test_import_artifacts_are_offline_and_preserve_staged_writer_safety(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             mock.patch.object(subprocess, "run", side_effect=AssertionError("execution")), \
             mock.patch.object(os, "getenv", side_effect=AssertionError("environment")):
            artifacts = self.bundle("lm-studio", "bionic")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            output = Path(temporary) / "bundle"
            validate_artifacts(artifacts)
            self.assertFalse(output.exists())
            write_artifacts(output, artifacts)
            target = output / artifacts[1].relative_path
            target.write_text("user changed file\n", encoding="utf-8")
            with self.assertRaises(WeaverError):
                write_artifacts(output, artifacts, force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "user changed file\n")
        self.assertEqual(validate_skills(ROOT), [])

    def test_new_evidence_has_fixture_checks_without_runtime_claims(self):
        for product, surface, capability in (("cline", "ide", "skills"), ("cursor", "ide", "skills"),
                ("qoder", "ide", "mcp"), ("qoder", "ide", "instructions"), ("qoder", "ide", "skills"),
                ("perplexity", "app", "skills"), ("perplexity", "orchestrator", "instructions"),
                ("lm-studio", "desktop", "instructions")):
            with self.subTest(product=product, surface=surface, capability=capability):
                evidence = get_integration(product, ROOT).surface(surface).capabilities[capability]
                self.assertEqual(evidence.checked_on, "2026-10-26")
                self.assertIn("tests/test_capability_expansion.py", evidence.fixture_tests)
                self.assertTrue(evidence.sources)
                self.assertEqual(evidence.runtime_checks, ())


if __name__ == "__main__":
    unittest.main()
