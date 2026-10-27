"""Air IDE handoffs preserve the boundary between presets and installed agents."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentweaver.adapters import render_mcp
from agentweaver.agents import agent_inventory, render_agent_definition
from agentweaver.config import WeaverError, load_json
from agentweaver.coverage import load_requested_scope
from agentweaver.integrations import get_integration
from agentweaver.models import load_model_hosts
from agentweaver.providers import render_provider_setup
from agentweaver.skills import render_instruction_bundle

ROOT = Path(__file__).resolve().parents[1]
PRESETS = "https://www.jetbrains.com/help/air/air-in-ides-presets.html"


class AirIdeTests(unittest.TestCase):
    def setUp(self):
        self.spec = get_integration("jetbrains-air", ROOT)
        self.profile = load_json(ROOT / "profiles/efficient.json")

    def test_ide_is_a_distinct_requested_surface_with_no_desktop_contract_transfer(self):
        self.assertEqual(self.spec.default_surface, "desktop")
        self.assertEqual(set(self.spec.surface_ids()), {"desktop", "ide"})
        item = next(item for item in load_requested_scope(ROOT) if item.id == "jetbrains-air")
        self.assertEqual({target.surface_id for target in item.targets}, {"desktop", "ide"})
        ide = self.spec.surface("ide")
        self.assertIsNone(ide.native_path_hint)
        self.assertEqual(ide.instruction_paths, ())
        self.assertIsNone(ide.skills_path)
        for name in ("mcp", "instructions", "skills"):
            self.assertEqual(ide.capabilities[name].state, "manual")
        for name in ("provider", "os"):
            self.assertEqual(ide.capabilities[name].state, "unknown")
        self.assertEqual(set(ide.os_workflows.values()), {"unknown"})
        self.assertIn(PRESETS, ide.capabilities["instructions"].sources)
        self.assertTrue(all(not evidence.runtime_checks for evidence in ide.capabilities.values()))
        hosts = {(row["product_id"], row["surface_id"]): row for row in load_model_hosts(ROOT)}
        self.assertEqual(hosts[("jetbrains-air", "desktop")]["reasoning"]["state"], "unknown")
        host = hosts[("jetbrains-air", "ide")]
        self.assertEqual(host["reasoning"]["state"], "manual")
        effort = next(control for control in host["reasoning"]["controls"] if control["kind"] == "effort")
        self.assertIsNone(effort["values"])
        self.assertEqual(host["availability"], "unknown")
        self.assertEqual(host["runtime_checks"], [])

    def test_bundle_stages_manual_context_and_preset_instructions_without_registering_skills(self):
        artifacts = render_instruction_bundle(self.spec, "ide", self.profile,
                                              ["token-efficient-retrieval"], ROOT)
        self.assertEqual([(item.relative_path.as_posix(), item.kind) for item in artifacts], [
            ("jetbrains-air/handoff/INSTRUCTIONS.md", "handoff"),
            ("jetbrains-air/read/token-efficient-retrieval.md", "read-file"),
        ])
        text = artifacts[0].content.decode()
        for token in ("Manage Presets", "Injected instructions", "Add > Project Files and Folders",
                      "Add > Skills", "does not register", "not an IDE configuration path"):
            self.assertIn(token, text)
        self.assertEqual(artifacts[1].content,
                         (ROOT / ".agents/skills/token-efficient-retrieval/SKILL.md").read_bytes()
                         .replace(b"\r\n", b"\n"))
        desktop = render_instruction_bundle(self.spec, "desktop", self.profile, [], ROOT)
        self.assertEqual(desktop[0].relative_path.as_posix(), "jetbrains-air/AGENTS.md")

    def test_custom_agent_route_is_only_a_preset_handoff_and_rejects_unapplied_overrides(self):
        row = next(row for row in agent_inventory(ROOT)["definitions"]
                   if (row["product_id"], row["surface_id"]) == ("jetbrains-air", "ide"))
        self.assertEqual(row["state"], "manual")
        self.assertIsNone(row["path_template"])
        values = dict(root=ROOT, name="reviewer", description="Review a change.",
                      instructions="Review the requested files.")
        artifacts = render_agent_definition(self.spec, "ide", **values)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].kind, "agent-handoff")
        text = artifacts[0].content.decode()
        for token in ("Manual Air IDE preset", "New Preset", "Injected instructions", "already running",
                      "not a registered subagent file", "no registration or executable",
                      "Review the requested files."):
            self.assertIn(token, text)
        for override in ({"model_id": "synthetic/model"}, {"effort": "high"},
                         {"provider_id": "openrouter"}, {"snapshot": {}}, {"llm_profile": "review"}):
            with self.subTest(override=override), self.assertRaises(WeaverError):
                render_agent_definition(self.spec, "ide", **values, **override)

    def test_unestablished_ide_mcp_and_openrouter_exports_fail_closed(self):
        with self.assertRaises(WeaverError):
            render_mcp(self.spec, "ide", self.profile, load_json(ROOT / "catalogs/mcp-servers.json"), [])
        with self.assertRaises(WeaverError):
            render_provider_setup(self.spec, "ide", provider_id="openrouter", model_id="synthetic/model")

    def test_cli_explicit_ide_dry_run_produces_only_manual_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "staged"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [sys.executable, "-m", "agentweaver", "bundle", "--root", str(ROOT),
                 "--agent", "JetBrainsAir", "--surface", "ide",
                 "--profile", "efficient", "--skill", "token-efficient-retrieval",
                 "--output-dir", str(output), "--dry-run"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual({item["kind"] for item in manifest["artifacts"]}, {"handoff", "read-file"})
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
