"""Air IDE handoffs preserve the boundary between presets and installed agents."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_cli_exports import invoke

from agentweaver.adapters import render_mcp
from agentweaver.agents import agent_inventory, render_acp_registration, render_agent_definition
from agentweaver.config import WeaverError, load_json
from agentweaver.coverage import coverage_report, load_requested_scope
from agentweaver.integrations import get_integration
from agentweaver.models import load_model_hosts
from agentweaver.skills import render_instruction_bundle

ROOT = Path(__file__).resolve().parents[1]
PRESETS = "https://www.jetbrains.com/help/air/air-in-ides-presets.html"
EXTENSIONS = "https://www.jetbrains.com/help/air/air-in-ides-extend-agent-capabilities.html"


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
        self.assertEqual(ide.skills_path, ".agents/skills")
        self.assertEqual(ide.config_scope, "user")
        self.assertEqual(ide.documented_location, "~/.jetbrains/mcp/mcp.json")
        for name in ("mcp", "instructions", "provider"):
            self.assertEqual(ide.capabilities[name].state, "manual")
        self.assertEqual(ide.capabilities["skills"].state, "available")
        for name in ("mcp", "skills"):
            self.assertIn(EXTENSIONS, ide.capabilities[name].sources)
        self.assertEqual(ide.capabilities["os"].state, "unknown")
        self.assertEqual(set(ide.os_workflows.values()), {"unknown"})
        self.assertIn(PRESETS, ide.capabilities["instructions"].sources)
        self.assertTrue(all(not evidence.runtime_checks for evidence in ide.capabilities.values()))
        hosts = {(row["product_id"], row["surface_id"]): row for row in load_model_hosts(ROOT)}
        self.assertEqual(hosts[("jetbrains-air", "desktop")]["reasoning"]["state"], "manual")
        host = hosts[("jetbrains-air", "ide")]
        self.assertEqual(host["reasoning"]["state"], "manual")
        effort = next(control for control in host["reasoning"]["controls"] if control["kind"] == "effort")
        self.assertIsNone(effort["values"])
        self.assertEqual(host["availability"], "unknown")
        self.assertEqual(host["runtime_checks"], [])

    def test_bundle_stages_project_skills_with_manual_preset_instructions(self):
        artifacts = render_instruction_bundle(self.spec, "ide", self.profile,
                                              ["token-efficient-retrieval"], ROOT)
        self.assertEqual([(item.relative_path.as_posix(), item.kind) for item in artifacts], [
            ("jetbrains-air/handoff/INSTRUCTIONS.md", "handoff"),
            ("jetbrains-air/.agents/skills/token-efficient-retrieval/SKILL.md", "skill"),
        ])
        text = artifacts[0].content.decode()
        for token in ("Manage Presets", "Injected instructions", "AI > Skills", "Add > Skills", ".agents/skills"):
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

    def test_acp_cli_stages_existing_agent_with_mcp_off_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "staged"
            command = r"C:\Program Files\Agent\agent.exe"
            base = ("acp", "--root", str(ROOT), "--agent", "jetbrains-air", "--surface", "ide", "--os", "windows",
                    "--name", "Example ACP agent", "--command", command, "--arg", "acp",
                    "--arg=--config", "--arg", "settings with spaces.json", "--output-dir", str(stage))
            with patch("agentweaver.cli.detect_host_os", return_value="windows-x64"), \
                 patch("socket.socket", side_effect=AssertionError("unexpected network")), \
                 patch("subprocess.Popen", side_effect=AssertionError("unexpected launch")):
                code, output, error = invoke(*base, "--dry-run")
                self.assertEqual((code, error), (0, ""))
                config, handoff = json.loads(output)["artifacts"]
                self.assertFalse(stage.exists())
                self.assertEqual(config["path"], "jetbrains-air/fragments/acp.ide.json")
                self.assertEqual(config["kind"], "acp-registration")
                self.assertEqual(json.loads(config["content"]), {"agent_servers": {"Example ACP agent": {
                    "command": command, "args": ["acp", "--config", "settings with spaces.json"],
                    "use_idea_mcp": False, "use_custom_mcp": False,
                }}})
                self.assertIn("~/.jetbrains/acp.json", handoff["content"])
                self.assertIn("own model, reasoning and authorization settings", handoff["content"])
                code, _, error = invoke(*base)
                self.assertEqual((code, error), (0, ""))
                self.assertEqual((stage / config["path"]).read_text(encoding="utf-8"), config["content"])

    def test_acp_preserves_separate_permission_choices_and_requires_target_absolute_paths(self):
        options = dict(name="Example agent", command="/usr/local/bin/agent", arguments=[], target_os="linux")
        for ide, custom in ((True, False), (False, True), (True, True)):
            with self.subTest(ide=ide, custom=custom):
                artifact, _ = render_acp_registration(self.spec, "ide", **options,
                                                      use_idea_mcp=ide, use_custom_mcp=custom)
                entry = json.loads(artifact.content)["agent_servers"]["Example agent"]
                self.assertEqual(entry, {"command": "/usr/local/bin/agent", "args": [],
                                         "use_idea_mcp": ide, "use_custom_mcp": custom})
        for override in ({"target_os": "windows"}, {"command": r"C:\Tools\agent.exe"}):
            with self.subTest(override=override), self.assertRaises(WeaverError):
                render_acp_registration(self.spec, "ide", **(options | override))

    def test_acp_rejects_command_expansion_credentials_and_wrong_surface(self):
        options = dict(name="Example agent", command="/usr/local/bin/agent", arguments=["acp"],
                       target_os="linux")
        with self.assertRaisesRegex(WeaverError, "JetBrains Air"):
            render_acp_registration(get_integration("codex", ROOT), "cli", **options)
        for override in ({"command": "agent"}, {"command": "/opt/test/../agent"},
                         {"command": "/opt/${AGENT}"}, {"arguments": ["--api-key", "fixture"]},
                         {"arguments": ["line\nbreak"]}, {"name": " Agent "}):
            with self.subTest(override=override), self.assertRaises(WeaverError):
                render_acp_registration(self.spec, "ide", **(options | override))

    def test_desktop_acp_stages_the_standalone_config_and_rejects_ide_mcp_switches(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "staged"
            command = r"C:\Program Files\Agent\agent.exe"
            args = ("acp", "--root", str(ROOT), "--agent", "jetbrains-air", "--surface", "desktop",
                    "--os", "windows", "--name", "Standalone agent", "--command", command,
                    "--arg", "settings with spaces.json", "--output-dir", str(stage))
            code, _, error = invoke(*args, "--use-custom-mcp")
            self.assertNotEqual(code, 0)
            self.assertIn("apply to Air in IDEs only", error)
            self.assertFalse(stage.exists())
            code, _, error = invoke(*args)
            self.assertEqual((code, error), (0, ""))
            folder = stage / "jetbrains-air/fragments"
            self.assertEqual(json.loads((folder / "acp.desktop.json").read_bytes()), {
                "agent_servers": {"Standalone agent": {"command": command, "args": ["settings with spaces.json"]}},
            })
            self.assertIn("standalone Air app's configuration directory",
                          (folder / "ACP-DESKTOP-HANDOFF.md").read_text(encoding="utf-8"))
            ide_args = tuple("ide" if value == "desktop" else value for value in args)
            code, _, error = invoke(*ide_args)
            self.assertEqual((code, error), (0, ""))
            self.assertTrue((folder / "acp.desktop.json").is_file())
            self.assertIn("~/.jetbrains/acp.json", (folder / "ACP-HANDOFF.md").read_text(encoding="utf-8"))

    def test_bundled_loader_observations_keep_standalone_and_ide_builds_separate(self):
        report = coverage_report(ROOT)
        air = next(item for item in report["requested_items"] if item["id"] == "jetbrains-air")
        targets = {row["surface_id"]: row for row in air["targets"]}
        desktop, = targets["desktop"]["runtime_observations"]
        self.assertEqual(desktop["product_version"], "262.1102.13")
        self.assertEqual(desktop["interface"], "desktop-bundled-config-loaders")
        self.assertEqual(desktop["environment"], "isolated-native-loader")
        self.assertEqual(desktop["os_variant"], "windows:11:10.0.26300.9711")
        self.assertEqual(desktop["architecture"], "x64")
        self.assertEqual({check["check"]: check["capability"] for check in desktop["checks"]}, {
            "air-desktop-acp-registration-load": "agents",
            "air-desktop-mcp-config-parse": "mcp",
            "air-desktop-skill-file-parse": "skills",
        })
        observations = targets["ide"]["runtime_observations"]
        self.assertEqual({row["product_version"] for row in observations}, {
            "262.8665.533-windows-x86_64", "263.7307.291-windows-x86_64",
        })
        for row in observations:
            self.assertEqual(row["interface"], "ide-bundled-config-loaders")
            self.assertEqual(row["environment"], "isolated-native-loader")
            self.assertEqual(row["os_variant"], "windows:11:10.0.26300.9711")
            self.assertEqual(row["architecture"], "x64")
            self.assertEqual({check["check"]: check["capability"] for check in row["checks"]}, {
                "air-ide-acp-registration-load": "agents",
                "air-ide-mcp-config-parse": "mcp",
            })

    def test_ide_mcp_stages_only_explicit_https_and_stdio_entries_for_manual_merge(self):
        catalog = {"servers": {
            "remote-fixture": {"kind": "remote", "url": "https://example.test/mcp",
                               "default_enabled": False, "description": "Remote fixture"},
            "local-fixture": {"kind": "stdio", "command": "python", "args": ["-I", "read only.py"],
                              "default_enabled": False, "description": "Local fixture"},
        }}
        profile = self.profile | {"allowed_mcp": list(catalog["servers"]), "max_mcp_servers": 2}
        empty, = render_mcp(self.spec, "ide", profile, catalog, [])
        self.assertEqual(json.loads(empty.content), {"mcpServers": {}})
        artifact, = render_mcp(self.spec, "ide", profile, catalog, ["remote-fixture", "local-fixture"])
        self.assertEqual(artifact.relative_path.as_posix(), "jetbrains-air/fragments/mcp.ide.json")
        self.assertEqual(artifact.kind, "mcp-fragment")
        self.assertEqual(json.loads(artifact.content), {"mcpServers": {
            "remote-fixture": {"url": "https://example.test/mcp"},
            "local-fixture": {"command": "python", "args": ["-I", "read only.py"]},
        }})
        catalog["servers"]["local-fixture"]["args"].append("")
        desktop, = render_mcp(self.spec, "desktop", profile, catalog, ["remote-fixture", "local-fixture"])
        self.assertEqual(desktop.relative_path.as_posix(), "jetbrains-air/.air/mcp.json")
        self.assertEqual(desktop.kind, "mcp")
        self.assertEqual(json.loads(desktop.content), {"mcpServers": {
            "remote-fixture": {"url": "https://example.test/mcp"},
            "local-fixture": {"command": "python", "args": ["-I", "read only.py", ""], "enabled": False},
        }})

    def test_cli_model_handoffs_use_the_documented_air_provider_and_effort_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "staged"
            env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
            for provider, display, model, reasoning_args, reasoning in (
                ("openrouter", "OpenRouter", "vendor/model", [], {}),
                ("openai", "OpenAI", "gpt-6-luna", ["--reasoning-effort", "high"], {"reasoning": {"effort": "high"}}),
                ("anthropic", "Anthropic", "claude-opus-5", [], {}),
                ("google", "Gemini", "models/gemini-3.8-flash", [], {}),
            ):
                with self.subTest(provider=provider):
                    result = subprocess.run(
                        [sys.executable, "-m", "agentweaver", "model", "--root", str(ROOT),
                         "--agent", "jetbrains-air", "--surface", "ide", "--provider", provider,
                         "--model", model, *reasoning_args, "--output-dir", str(output), "--dry-run"],
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    artifacts = {item["kind"]: item["content"] for item in json.loads(result.stdout)["artifacts"]}
                    self.assertEqual(set(artifacts), {"model-selection", "model-setup", "handoff"})
                    self.assertEqual(json.loads(artifacts["model-selection"]), {"model": model, **reasoning})
                    handoff = artifacts["model-setup"]
                    self.assertIn(f"More Providers, configure {display}", handoff)
                    self.assertIn(f"Look for `{model}`", handoff)
                    self.assertIn("check the Effort control", handoff)
                    self.assertIn("Keep the model-selection fragment as a provider API reference", handoff)
            self.assertFalse(output.exists())

    def test_cli_openrouter_provider_handoff_is_ide_only(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "staged"
            env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
            cmd = [sys.executable, "-m", "agentweaver", "provider", "--root", str(ROOT),
                   "--agent", "jetbrains-air", "--provider", "openrouter", "--model", "vendor/model",
                   "--output-dir", str(output), "--dry-run"]
            ide = subprocess.run([*cmd, "--surface", "ide"], cwd=ROOT, env=env,
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(ide.returncode, 0, ide.stderr)
            artifacts = {item["kind"]: item["path"] for item in json.loads(ide.stdout)["artifacts"]}
            self.assertEqual(set(artifacts), {"provider-setup", "handoff"})
            self.assertEqual(artifacts["provider-setup"], "jetbrains-air/provider/provider-setup.md")
            desktop = subprocess.run([*cmd, "--surface", "desktop"], cwd=ROOT, env=env,
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(desktop.returncode, 2)
            self.assertFalse(output.exists())

    def test_cli_dry_runs_stage_skills_and_mcp_without_writing_settings(self):
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
            self.assertEqual({item["kind"] for item in manifest["artifacts"]}, {"handoff", "skill"})
            result = subprocess.run(
                [sys.executable, "-m", "agentweaver", "render", "--root", str(ROOT),
                 "--agent", "JetBrainsAir", "--surface", "ide", "--profile", "efficient",
                 "--enable", "context7", "--output-dir", str(output), "--dry-run"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            artifacts = {item["kind"]: item for item in json.loads(result.stdout)["artifacts"]}
            self.assertEqual(artifacts["mcp-fragment"]["path"], "jetbrains-air/fragments/mcp.ide.json")
            self.assertIn("~/.jetbrains/mcp/mcp.json", artifacts["handoff"]["content"])
            self.assertIn("Edit MCPs", artifacts["handoff"]["content"])
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
