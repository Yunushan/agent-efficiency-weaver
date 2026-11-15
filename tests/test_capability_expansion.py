"""Offline fixtures for first-party IDE schemas and manual Skill import boundaries."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from zipfile import ZipFile

from test_cli_exports import invoke

from agentweaver.adapters import render_mcp, stdio_inventory
from agentweaver.artifacts import validate_artifacts, write_artifacts
from agentweaver.config import WeaverError, load_json
from agentweaver.integrations import get_integration
from agentweaver.skills import (
    render_chatgpt_plugin,
    render_instruction_bundle,
    render_perplexity_plugin,
    validate_skills,
)

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
        for product, surface, ui in (("lm-studio", "bionic", "Settings > Skills > Add Skill > Install a skill"),
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
        for product, surface in (("perplexity", "orchestrator"), ("lm-studio", "desktop"), ("lm-studio", "api")):
            with self.subTest(product=product, surface=surface):
                spec = get_integration(product, ROOT)
                self.assertEqual(spec.surface(surface).capabilities["skills"].state, "manual")
                artifacts = self.bundle(product, surface)
                self.assertEqual([item.kind for item in artifacts], ["handoff", "read-file"])
                self.assertNotIn("imports/skills", artifacts[1].relative_path.as_posix())
        text = self.bundle("perplexity", "orchestrator")[0].content.decode()
        self.assertIn("client-side Skill tool", text)
        self.assertIn("no native on-demand discovery is claimed", text)
        self.assertIn("Bionic is a separate app", self.bundle("lm-studio", "desktop")[0].content.decode())

    def test_lmstudio_skill_mcp_runs_the_exact_staged_read_only_tool(self):
        with tempfile.TemporaryDirectory(prefix="weaver skills ") as directory:
            output = Path(directory)
            common = ("bundle", "--root", str(ROOT), "--agent", "lm-studio", "--profile", "efficient",
                      "--output-dir", directory)
            desktop = invoke(*common, "--surface", "desktop", "--lmstudio-skill-mcp", "--skill", SKILL, "--dry-run")
            self.assertEqual((desktop[0], desktop[2]), (0, ""))
            self.assertNotIn("chat-integration.json", desktop[1])
            self.assertEqual(list(output.iterdir()), [])
            code, _, error = invoke(*common, "--surface", "api", "--lmstudio-skill-mcp", "--skill", SKILL)
            self.assertEqual((code, error), (0, ""))
            staged = output / "lm-studio/skills-mcp"
            fragment = json.loads((staged / "mcp.fragment.json").read_bytes())
            command = fragment["mcpServers"]["weaver-skills"]
            self.assertEqual(command, {"type": "stdio", "command": str(Path(sys.executable).absolute()),
                                       "args": ["-I", str(staged / "skill-server.py")]})
            self.assertEqual(json.loads((staged / "chat-integration.json").read_bytes()), {
                "integrations": [{"type": "plugin", "id": "mcp/weaver-skills", "allowed_tools": ["load_skill"]}],
            })
            calls = [
                ("tools/list", {}, 1),
                ("initialize", {"protocolVersion": "2024-11-05"}, "init"),
                ("notifications/initialized", {}, None),
                ("ping", {}, 2),
                ("tools/list", {}, 3),
                ("tools/call", {"name": "load_skill", "arguments": {"name": SKILL}}, 4),
                ("tools/call", {"name": "load_skill", "arguments": {"name": "benchmark-design"}}, 5),
                ("tools/call", {"name": "load_skill", "arguments": {"name": "../security-review"}}, 6),
                ("tools/call", {"name": "load_skill", "arguments": {"name": SKILL, "path": "secret"}}, 7),
                ("tools/call", {"name": "run_script", "arguments": {"name": SKILL}}, 8),
            ]
            wire = "\n".join(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}
                                        | ({"id": identifier} if identifier is not None else {}))
                             for method, params, identifier in calls) + "\n"
            wire += '{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"load_skill",' \
                    '"arguments":{"name":"benchmark-design","name":"security-review"}}}\n'
            run = subprocess.run([command["command"], *command["args"]], input=wire.encode(), cwd=output,
                                 capture_output=True, timeout=12, check=True)
            self.assertEqual(run.stderr, b"")
            replies = {item["id"]: item for item in map(json.loads, run.stdout.splitlines())}
            self.assertEqual(set(replies), {1, "init", 2, 3, 4, 5, 6, 7, 8, None})
            self.assertEqual(replies[1]["error"]["code"], -32600)
            self.assertEqual(replies["init"]["result"]["protocolVersion"], "2024-11-05")
            self.assertEqual(replies[2]["result"], {})
            tool, = replies[3]["result"]["tools"]
            self.assertEqual(tool["name"], "load_skill")
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertEqual(tool["inputSchema"]["properties"]["name"]["enum"], [SKILL])
            body = ((ROOT / ".agents/skills" / SKILL / "SKILL.md").read_text(encoding="utf-8")
                    .partition("\n---\n")[2].removeprefix("\n"))
            self.assertNotIn(body, tool["description"])
            self.assertEqual(replies[4]["result"], {"content": [{"type": "text", "text": body}]})
            for identifier in (5, 6, 7, 8):
                self.assertEqual(replies[identifier]["error"]["code"], -32602)
                self.assertNotIn("result", replies[identifier])
            self.assertEqual(replies[None]["error"]["code"], -32700)

    def test_lmstudio_skill_mcp_needs_explicit_scope_and_skill_selection(self):
        base = ("bundle", "--root", str(ROOT), "--agent", "lm-studio", "--dry-run", "--lmstudio-skill-mcp")
        for flags, error in (
            (("--profile", "efficient", "--surface", "api"), "at least one Skill"),
            (("--profile", "enterprise-secure", "--surface", "desktop", "--skill", SKILL), "no MCP server capacity"),
            (("--profile", "efficient", "--surface", "bionic", "--skill", SKILL), "classic Desktop or the native API"),
        ):
            with self.subTest(flags=flags):
                code, output, diagnostic = invoke(*base, *flags)
                self.assertEqual((code, output), (2, ""))
                self.assertIn(error, diagnostic)

    def test_lmstudio_classic_preset_stages_system_prompt_and_optional_reasoning_fields(self):
        common = ("bundle", "--root", str(ROOT), "--agent", "lm-studio", "--surface", "desktop",
                  "--profile", "efficient", "--dry-run")
        code, output, error = invoke(*common, "--lmstudio-preset", "--lmstudio-preset-thinking", "on",
                                     "--lmstudio-preset-budget", "128", "--skill", SKILL)
        self.assertEqual(code, 0, error)
        artifacts = {artifact["path"]: artifact for artifact in json.loads(output)["artifacts"]}
        fragment = artifacts["lm-studio/imports/presets/weaver-efficient.preset.json"]
        self.assertEqual(fragment["kind"], "preset-import")
        payload = json.loads(fragment["content"])
        self.assertEqual(set(payload), {"identifier", "name", "operation", "load"})
        self.assertEqual(payload["identifier"], "@local:weaver-efficient")
        self.assertEqual(payload["load"], {"fields": []})
        fields = {row["key"]: row["value"] for row in payload["operation"]["fields"]}
        self.assertIn("Avoid full-repository dumps", fields["llm.prediction.systemPrompt"])
        self.assertEqual(fields["llm.prediction.reasoning.budgetTokens"], {"checked": True, "value": 128})
        self.assertIs(fields["llm.prediction.reasoning.enableThinking"], True)
        self.assertIn("Presets dropdown", artifacts["lm-studio/handoff/INSTRUCTIONS.md"]["content"])
        self.assertEqual(artifacts[f"lm-studio/read/{SKILL}.md"]["kind"], "read-file")
        for options, expected in (((), {}), (("--lmstudio-preset-thinking", "off"), {
            "llm.prediction.reasoning.enableThinking": False,
        }), (("--lmstudio-preset-budget", "0"), {
            "llm.prediction.reasoning.budgetTokens": {"checked": True, "value": 0},
        })):
            with self.subTest(options=options):
                code, output, error = invoke(*common, "--lmstudio-preset", *options)
                self.assertEqual(code, 0, error)
                preset = next(item for item in json.loads(output)["artifacts"] if item["kind"] == "preset-import")
                values = {row["key"]: row["value"] for row in json.loads(preset["content"])["operation"]["fields"]}
                del values["llm.prediction.systemPrompt"]
                self.assertEqual(values, expected)

    def test_lmstudio_classic_preset_rejects_unrelated_routes_and_invalid_budgets(self):
        common = ("bundle", "--root", str(ROOT), "--agent", "lm-studio", "--profile", "efficient", "--dry-run")
        for extra in (("--surface", "bionic", "--lmstudio-preset"), ("--surface", "desktop", "--lmstudio-preset-budget", "0"),
                      ("--surface", "desktop", "--lmstudio-preset", "--lmstudio-preset-budget", "-1"),
                      ("--surface", "desktop", "--lmstudio-preset", "--lmstudio-preset-budget", str(2**53)),
                      ("--surface", "desktop", "--lmstudio-preset", "--perplexity-plugin")):
            with self.subTest(extra=extra):
                self.assertEqual(invoke(*common, *extra)[0], 2)

    def test_perplexity_agent_api_stages_project_skill_upload_payloads(self):
        handoff, skill = self.bundle("perplexity", "agent-api")
        self.assertEqual(skill.kind, "skill-import")
        self.assertEqual(skill.relative_path.parent.parent.as_posix(), "perplexity/imports/skills")
        self.assertEqual(skill.relative_path.name, "SKILL.md")
        self.assertEqual(skill.content, (ROOT / ".agents/skills" / skill.relative_path.parent.name / "SKILL.md")
                         .read_bytes().replace(b"\r\n", b"\n"))
        text = handoff.content.decode()
        for token in ("zipfile -c NAME.zip NAME", "API Portal", "project-owned ID", "reviewed revision"):
            self.assertIn(token, text)
        self.assertNotIn("Perplexity Computer", text)
        self.assertEqual(len(self.bundle("perplexity", "agent-api", [])), 1)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            write_artifacts(destination, [handoff, skill])
            name = skill.relative_path.parent.name
            imports = destination / skill.relative_path.parent.parent
            subprocess.run([sys.executable, "-m", "zipfile", "-c", f"{name}.zip", name],
                           cwd=imports, check=True, capture_output=True, timeout=30)
            with ZipFile(imports / f"{name}.zip") as archive:
                self.assertEqual([item.filename for item in archive.infolist() if not item.is_dir()],
                                 [f"{name}/SKILL.md"])
                self.assertEqual(archive.read(f"{name}/SKILL.md"), skill.content)

    def test_unestablished_mcp_schemas_do_not_inherit_other_surfaces(self):
        spec = get_integration("perplexity", ROOT)
        with self.assertRaises(WeaverError):
            render_mcp(spec, "orchestrator", self.profile, self.catalog, [])
        self.assertIsNone(spec.surface("orchestrator").mcp_dialect)
        bionic = get_integration("lm-studio", ROOT).surface("bionic")
        self.assertIsNone(bionic.mcp_dialect)
        self.assertIsNone(bionic.native_path_hint)

    def test_chatgpt_plugin_stages_portable_files_and_local_marketplace_only_for_desktop(self):
        for surface in ("desktop", "web", "work-local", "work-cloud"):
            with self.subTest(surface=surface):
                code, output, error = invoke("bundle", "--root", str(ROOT), "--agent", "chatgpt",
                                             "--surface", surface, "--profile", "efficient", "--chatgpt-plugin",
                                             "--skill", SKILL, "--enable", "context7", "--dry-run")
                self.assertEqual((code, error), (0, ""))
                artifacts = {item["kind"]: item for item in json.loads(output)["artifacts"]}
                self.assertEqual(set(artifacts) - {"plugin-marketplace"},
                                 {"plugin-manifest", "plugin-mcp", "skill", "handoff"})
                manifest = json.loads(artifacts["plugin-manifest"]["content"])
                self.assertEqual(manifest["name"], "weaver-workflows")
                self.assertEqual(manifest["$schema"], "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
                self.assertTrue(artifacts["plugin-manifest"]["path"].startswith(f"chatgpt/plugin-staging/{surface}/"))
                self.assertTrue(artifacts["skill"]["path"].endswith(f"/skills/{SKILL}/SKILL.md"))
                self.assertEqual(json.loads(artifacts["plugin-mcp"]["content"])["mcpServers"], {
                    "context7": {"type": "streamable-http", "url": "https://mcp.context7.com/mcp"},
                })
                if surface in {"desktop", "work-local"}:
                    entry, = json.loads(artifacts["plugin-marketplace"]["content"])["plugins"]
                    self.assertEqual(entry["source"], {"source": "local", "path": "./plugins/weaver-workflows"})
                    self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
                else:
                    self.assertNotIn("plugin-marketplace", artifacts)

    def test_chatgpt_plugin_isolates_selections_and_only_allows_stdio_for_local_targets(self):
        chatgpt = get_integration("chatgpt", ROOT)
        profile = self.profile | {"allowed_mcp": ["context7", "local", "templated", "absolute", "escape", "bundled"]}
        catalog = {"servers": {
            "context7": self.catalog["servers"]["context7"],
            "templated": self.catalog["servers"]["context7"] | {"url": "https://example.com/mcp/%24%7BSECRET%7D"},
            "local": {"kind": "stdio", "command": "python", "args": ["fixture.py", ""], "default_enabled": False},
            "absolute": {"kind": "stdio", "command": "C:/bin/fixture.exe", "args": [], "default_enabled": False},
            "escape": {"kind": "stdio", "command": "./bin/../../outside", "args": [], "default_enabled": False},
            "bundled": {"kind": "stdio", "command": "./bin/fixture.exe", "args": [], "default_enabled": False},
        }}

        def render(surface, skills, servers):
            return render_chatgpt_plugin(chatgpt, surface, ROOT, profile, catalog, skills, servers)

        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")):
            local = render("work-local", [SKILL], ["local"])
            skills_only = render("work-local", [SKILL], [])
        manifest, = [item for item in local if item.kind == "plugin-manifest"]
        pipe, = [item for item in local if item.kind == "plugin-mcp"]
        self.assertEqual(json.loads(manifest.content)["extensions"], {"com.openai": {"requires_local_executor": True}})
        self.assertEqual(json.loads(pipe.content)["mcpServers"], {
            "local": {"type": "stdio", "command": "python", "args": ["fixture.py", ""]},
        })
        self.assertTrue({entry.relative_path for entry in local}.isdisjoint(
            entry.relative_path for entry in skills_only))
        self.assertNotIn("plugin-mcp", {entry.kind for entry in skills_only})
        for surface in ("web", "work-cloud"):
            with self.assertRaisesRegex(WeaverError, "stdio servers require"):
                render(surface, [SKILL], ["local"])
        for selected, error in ((["templated"], "literal HTTPS"), (["missing"], "not permitted")):
            with self.assertRaisesRegex(WeaverError, error):
                render("work-local", [SKILL], selected)
        with self.assertRaisesRegex(WeaverError, "Select at least one"):
            render("work-local", [], [])
        for name in ("absolute", "escape"):
            with self.subTest(name=name), self.assertRaisesRegex(WeaverError, "bare executable name"):
                render("work-local", [SKILL], [name])
        bundled, = [entry for entry in render("work-local", [], ["bundled"]) if entry.kind == "plugin-mcp"]
        self.assertEqual(json.loads(bundled.content)["mcpServers"]["bundled"]["command"], "./bin/fixture.exe")

    def test_chatgpt_plugin_name_keeps_manifest_paths_and_marketplace_entry_in_sync(self):
        name = "repository-review"
        for surface in ("desktop", "web", "work-local", "work-cloud"):
            base = ("bundle", "--root", str(ROOT), "--agent", "chatgpt", "--surface", surface,
                    "--profile", "efficient", "--chatgpt-plugin", "--skill", SKILL, "--dry-run")
            with self.subTest(surface=surface):
                original = invoke(*base)[1]
                self.assertEqual(invoke(*base, "--chatgpt-plugin-name", "weaver-workflows")[1], original)
                code, output, error = invoke(*base, "--chatgpt-plugin-name", name)
                self.assertEqual((code, error), (0, ""))
                artifacts = {item["kind"]: item for item in json.loads(output)["artifacts"]}
                manifest = artifacts["plugin-manifest"]
                self.assertEqual(json.loads(manifest["content"])["name"], name)
                self.assertTrue(manifest["path"].endswith(f"/plugins/{name}/plugin.json"))
                self.assertTrue(artifacts["skill"]["path"].endswith(f"/plugins/{name}/skills/{SKILL}/SKILL.md"))
                staged_handoff, = (item for item in json.loads(output)["artifacts"]
                                   if item["path"].endswith("/PLUGIN-HANDOFF.md"))
                default_handoff, = (item for item in json.loads(original)["artifacts"]
                                    if item["path"].endswith("/PLUGIN-HANDOFF.md"))
                self.assertNotEqual(staged_handoff["path"], default_handoff["path"])
                self.assertIn(f"../{name}.zip", staged_handoff["content"])
                if surface in {"desktop", "work-local"}:
                    entry, = json.loads(artifacts["plugin-marketplace"]["content"])["plugins"]
                    self.assertEqual(entry["name"], name)
                    self.assertEqual(entry["source"]["path"], f"./plugins/{name}")
                else:
                    self.assertNotIn("plugin-marketplace", artifacts)

    def test_chatgpt_plugin_name_rejects_invalid_identifiers_and_requires_the_plugin_route(self):
        base = ("bundle", "--root", str(ROOT), "--agent", "chatgpt", "--surface", "desktop",
                "--profile", "efficient", "--skill", SKILL, "--dry-run")
        code, _, error = invoke(*base, "--chatgpt-plugin-name", "repository-review")
        self.assertEqual(code, 2)
        self.assertIn("requires --chatgpt-plugin", error)
        for name in ("", "../escape", "nested/dir", "Upper", "has_dot", "split--id", "aux", "com1", "x" * 65):
            with self.subTest(name=name):
                code, _, error = invoke(*base, "--chatgpt-plugin", "--chatgpt-plugin-name", name)
                self.assertEqual(code, 2)
                self.assertIn("portable lowercase ID", error)

    def test_chatgpt_workspace_marketplace_keeps_app_references_distinct_from_server_declarations(self):
        base = ("bundle", "--root", str(ROOT), "--agent", "chatgpt", "--surface", "work-cloud",
                "--profile", "efficient")
        app = ("--chatgpt-app", "docs:asdk_app_example")
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")):
            code, output, error = invoke(*base, "--chatgpt-plugin", "--chatgpt-workspace-marketplace", *app,
                                         "--chatgpt-app", "issues:connector_example",
                                         "--chatgpt-app", "knowledge:templated_apps_example", "--dry-run")
        self.assertEqual((code, error), (0, ""))
        artifacts = json.loads(output)["artifacts"]
        plan = {item["kind"]: item for item in artifacts}
        handoff, = [item["content"] for item in artifacts if item["path"].endswith("/PLUGIN-HANDOFF.md")]
        self.assertNotIn("plugin-mcp", plan)
        self.assertIn("Admin > Plugins", handoff)
        self.assertNotIn("python -c", handoff)
        self.assertEqual(json.loads(plan["plugin-manifest"]["content"])["extensions"], {
            "com.openai": {"apps": "./.app.json"},
        })
        self.assertTrue(plan["plugin-apps"]["path"].endswith("/plugins/weaver-workflows/.app.json"))
        self.assertEqual(json.loads(plan["plugin-apps"]["content"]), {"apps": {
            "docs": {"id": "asdk_app_example", "required": True},
            "issues": {"id": "connector_example", "required": True},
            "knowledge": {"id": "templated_apps_example", "required": True},
        }})
        self.assertTrue(plan["plugin-marketplace"]["path"].endswith("/.agents/plugins/marketplace.json"))
        marketplace = json.loads(plan["plugin-marketplace"]["content"])
        self.assertEqual(marketplace["name"], "weaver-workspace")
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/weaver-workflows")
        for args in (app, ("--chatgpt-workspace-marketplace",),
                     ("--chatgpt-plugin", *app),
                     ("--chatgpt-plugin", "--chatgpt-workspace-marketplace", "--chatgpt-app", "docs:plugin_plugin_asdk_app_x"),
                     ("--chatgpt-plugin", "--chatgpt-workspace-marketplace", "--chatgpt-app", "docs:Plugin_unrelated"),
                     ("--chatgpt-plugin", "--chatgpt-workspace-marketplace", *app,
                      "--chatgpt-app", "Docs:connector_other")):
            with self.subTest(args=args):
                self.assertEqual(invoke(*base, *args, "--dry-run")[0], 2)

    def test_chatgpt_app_listing_ids_preserve_the_canonical_package(self):
        base = ("bundle", "--root", str(ROOT), "--agent", "chatgpt", "--profile", "efficient",
                "--chatgpt-plugin", "--chatgpt-workspace-marketplace", "--dry-run")
        for surface in ("web", "work-cloud"):
            with self.subTest(surface=surface), mock.patch.object(socket, "socket", side_effect=AssertionError("network used")):
                args = (*base, "--surface", surface)
                canonical = invoke(*args, "--chatgpt-app", "docs:asdk_app_example",
                                   "--chatgpt-app", "issues:connector_example",
                                   "--chatgpt-app", "knowledge:templated_apps_example")
                self.assertEqual((canonical[0], canonical[2]), (0, ""))
                self.assertEqual(invoke(*args, "--chatgpt-app", "docs:plugin_asdk_app_example",
                                        "--chatgpt-app", "issues:plugin_connector_example",
                                        "--chatgpt-app", "knowledge:plugin_templated_apps_example"), canonical)

    def test_perplexity_portable_plugin_packages_skills_and_allowlisted_connectors(self):
        base = ("bundle", "--root", str(ROOT), "--agent", "perplexity", "--surface", "app",
                "--profile", "efficient")
        args = ("--perplexity-plugin", "--skill", SKILL, "--enable", "context7")
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")):
            fixtures = render_perplexity_plugin(get_integration("perplexity", ROOT), "app", ROOT,
                                                self.profile, self.catalog, [SKILL], ["context7"], [])
        code, output, error = invoke(*base, *args, "--dry-run")
        self.assertEqual((code, error), (0, ""))
        plan = json.loads(output)["artifacts"]
        files = {row["path"].removeprefix("perplexity/plugins/weaver-workflows/"): row["content"] for row in plan
                 if row["kind"] in {"plugin-manifest", "plugin-mcp", "skill"}}
        self.assertEqual(files, {item.relative_path.as_posix().removeprefix("perplexity/plugins/weaver-workflows/"):
                                item.content.decode() for item in fixtures if item.kind != "handoff"})
        prefix = "https://agent-plugins.org/schemas/1.0.0"
        self.assertEqual(json.loads(files["plugin.json"])["$schema"], prefix + "/plugin.schema.json")
        self.assertEqual(json.loads(files["mcp.json"]), {
            "$schema": prefix + "/mcp.schema.json",
            "mcpServers": {"context7": {"type": "streamable-http", "url": "https://mcp.context7.com/mcp"}},
        })
        canonical = (ROOT / ".agents/skills" / SKILL / "SKILL.md").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(files[f"skills/{SKILL}/SKILL.md"].encode(), canonical)
        with tempfile.TemporaryDirectory(dir=ROOT) as stage:
            code, _, error = invoke(*base, *args, "--output-dir", stage)
            self.assertEqual((code, error), (0, ""))
            package = Path(stage, "perplexity/plugins/weaver-workflows")
            handoff = (package.parent / "PLUGIN-HANDOFF.md").read_text(encoding="utf-8")
            command = next(line for line in handoff.splitlines() if line.startswith("python -c "))
            archive = package.parent / "weaver-workflows.zip"
            # A previously staged file must stay out of the archive for this selection.
            extra = package / "skills/unselected/SKILL.md"
            extra.parent.mkdir()
            extra.write_bytes(canonical)
            subprocess.run([sys.executable, "-c", command.removeprefix('python -c "').removesuffix('"')],
                           cwd=package, check=True, capture_output=True, timeout=30)
            with ZipFile(archive) as uploaded:
                self.assertEqual(set(uploaded.namelist()), set(files))
                self.assertEqual(uploaded.read(f"skills/{SKILL}/SKILL.md"), canonical)
        self.assertEqual(invoke(*base, "--enable", "context7", "--dry-run")[0], 2)
        self.assertEqual(invoke(*base, "--perplexity-plugin", "--dry-run")[0], 2)
        self.assertEqual(invoke(*base, "--perplexity-plugin", "--enable", "context7",
                                "--enable-sse", "context7", "--dry-run")[0], 2)
        self.assertEqual(invoke("bundle", "--root", str(ROOT), "--agent", "codex", "--surface", "cli",
                                "--profile", "efficient", *args, "--dry-run")[0], 2)

    def test_perplexity_plugin_preserves_explicit_sse_and_rejects_unsupported_servers(self):
        spec = get_integration("perplexity", ROOT)
        remote = self.catalog["servers"]["context7"]
        profile = self.profile | {"allowed_mcp": ["legacy", "local", "templated"]}
        catalog = {"servers": {
            "legacy": remote,
            "local": {"kind": "stdio", "command": "python", "args": ["fixture.py"], "default_enabled": False},
            "templated": remote | {"url": "https://example.com/mcp/%24%7BPLUGIN_DATA%7D"},
        }}
        artifacts = render_perplexity_plugin(spec, "app", ROOT, profile, catalog, [], [], ["legacy"])
        mcp, = [item for item in artifacts if item.kind == "plugin-mcp"]
        self.assertEqual(json.loads(mcp.content)["mcpServers"]["legacy"]["type"], "sse")
        skills_only = render_perplexity_plugin(spec, "app", ROOT, profile, catalog, [SKILL], [], [])
        self.assertNotIn("plugin-mcp", [item.kind for item in skills_only])
        for name, error in (("local", "remote HTTPS"), ("templated", "literal HTTPS"), ("missing", "not permitted")):
            with self.subTest(name=name), self.assertRaisesRegex(WeaverError, error):
                render_perplexity_plugin(spec, "app", ROOT, profile, catalog, [SKILL], [name], [])

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
