"""Independent parsing and safety checks for offline custom-agent definitions."""
import copy
import json
import socket
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import yaml

from agentweaver.agents import agent_inventory, render_agent_definition
from agentweaver.artifacts import write_artifacts
from agentweaver.config import WeaverError
from agentweaver.integrations import load_integrations

ROOT = Path(__file__).resolve().parents[1]
NATIVE_PATHS = {
    ("codex", "cli"): ".codex/agents/reviewer.toml",
    ("codex", "desktop"): ".codex/agents/reviewer.toml",
    ("codex", "ide"): ".codex/agents/reviewer.toml",
    ("claude-code", "cli"): ".claude/agents/reviewer.md",
    ("copilot", "vscode"): ".github/agents/reviewer.agent.md",
    ("factory", "cli"): ".factory/droids/reviewer.md",
    ("opencode", "cli"): ".opencode/agents/reviewer.md",
    ("kilo", "cli"): ".kilo/agents/reviewer.md",
    ("kilo", "ide"): ".kilo/agents/reviewer.md",
    ("qwen-code", "cli"): ".qwen/agents/reviewer.md",
    ("kimi-code", "cli"): ".kimi-code/agents/reviewer.md",
    ("cursor", "ide"): ".cursor/agents/reviewer.md",
    ("kiro", "ide"): ".kiro/agents/reviewer.json",
    ("qoder", "cli"): ".qoder/agents/reviewer.md",
    ("qoder", "ide"): ".qoder/agents/reviewer.md",
    ("antigravity", "ide"): ".agents/agents/reviewer.md",
    ("devin", "desktop-local"): ".devin/agents/reviewer.md",
    ("openhands", "cli"): ".agents/agents/reviewer.md",
}


def parse_definition(content, path):
    text = content.decode("utf-8")
    if path.endswith(".toml"):
        return tomllib.loads(text), None
    if path.endswith(".json"):
        return json.loads(text), None
    header, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(header), body.lstrip("\n")


class CustomAgentDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.integrations = load_integrations(ROOT)

    def render(self, product="codex", surface="cli", **kwargs):
        values = dict(name="reviewer", description="Review small changes.",
                      instructions="Inspect the requested code and report evidence.")
        values.update(kwargs)
        return render_agent_definition(self.integrations[product], surface, root=ROOT, **values)

    def test_inventory_covers_registered_surfaces_and_distinguishes_evidence(self):
        inventory = agent_inventory(ROOT)
        expected = {(product, surface.id) for product, spec in self.integrations.items() for surface in spec.surfaces}
        rows = inventory["definitions"]
        actual = {(row["product_id"], row["surface_id"]) for row in rows}
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(rows))
        self.assertEqual({row["state"] for row in rows}, {"native", "manual", "unknown", "unsupported"})
        self.assertFalse(inventory["network_used"])
        self.assertFalse(inventory["credentials_read"])
        self.assertFalse(inventory["runtime_verified"])
        native = {(row["product_id"], row["surface_id"]) for row in rows if row["state"] == "native"}
        self.assertEqual(native, set(NATIVE_PATHS))
        for row in rows:
            with self.subTest(product=row["product_id"], surface=row["surface_id"]):
                self.assertFalse(row["evidence"]["runtime_checks"])
                if row["state"] in {"native", "manual"}:
                    self.assertTrue(row["evidence"]["sources"])
                    expected_date = ("2026-10-27" if (row["product_id"], row["surface_id"]) ==
                                     ("jetbrains-air", "ide") else "2026-10-26")
                    self.assertEqual(row["evidence"]["checked_on"], expected_date)
                    self.assertIn("tests/test_agent_definitions.py", row["evidence"]["fixture_tests"])

    def test_every_native_file_independently_parses_and_has_separate_handoff(self):
        for (product, surface), path in NATIVE_PATHS.items():
            with self.subTest(product=product, surface=surface):
                artifacts = self.render(product, surface)
                self.assertEqual([item.kind for item in artifacts], ["agent-definition", "agent-handoff"])
                self.assertEqual(artifacts[0].relative_path.as_posix(), f"{product}/{path}")
                self.assertEqual(artifacts[1].relative_path.as_posix(), f"{product}/agents/reviewer/HANDOFF.md")
                parsed, body = parse_definition(artifacts[0].content, path)
                self.assertEqual(parsed["description"], "Review small changes.")
                if product == "codex":
                    self.assertIn("Inspect the requested code", parsed["developer_instructions"])
                elif product == "kiro":
                    self.assertIn("Inspect the requested code", parsed["prompt"])
                else:
                    self.assertIn("Inspect the requested code", body)
                for item in artifacts:
                    self.assertNotIn(b"\r", item.content)
                handoff = artifacts[1].content.decode()
                self.assertIn("No agent was installed or executed", handoff)
                self.assertIn("No maximum reasoning is selected automatically", handoff)
                self.assertIn("none recorded", handoff)

    def test_defaults_follow_host_without_implicit_model_or_effort_override(self):
        for (product, surface), path in NATIVE_PATHS.items():
            with self.subTest(product=product, surface=surface):
                parsed, _ = parse_definition(self.render(product, surface)[0].content, path)
                self.assertIn(parsed.get("model"), (None, "inherit"))
                self.assertNotIn("model_reasoning_effort", parsed)
                self.assertNotIn("reasoningEffort", parsed)
                self.assertNotIn("effort", parsed)

    def test_tool_presets_do_not_grant_parent_permissions_or_mcp_access(self):
        codex = tomllib.loads(self.render()[0].content.decode())
        self.assertEqual(codex["features"], dict(shell_tool=False, apps=False, plugins=False))
        for ignored_parent_key in ("sandbox_mode", "approval_policy", "model_provider", "mcp_servers"):
            self.assertNotIn(ignored_parent_key, codex)
        self.assertIn("do not override the parent", self.render()[1].content.decode())
        self.assertIn("no read-only OS sandbox is promised", self.render()[1].content.decode())
        for product, surface in (("claude-code", "cli"), ("qwen-code", "cli"), ("kimi-code", "cli"),
                                 ("factory", "cli"), ("qoder", "cli"), ("qoder", "ide")):
            path = NATIVE_PATHS[product, surface]
            parsed, _ = parse_definition(self.render(product, surface)[0].content, path)
            self.assertNotIn("Bash", parsed["tools"])
            self.assertNotIn("permissionMode", parsed)
            if product in {"factory", "qoder"}:
                self.assertEqual(parsed["mcpServers"], [])
        opencode, _ = parse_definition(self.render("opencode")[0].content, NATIVE_PATHS["opencode", "cli"])
        self.assertEqual(opencode["permission"]["*"], "deny")
        self.assertEqual(opencode["mode"], "subagent")
        kiro = json.loads(self.render("kiro", "ide")[0].content)
        self.assertEqual(kiro["tools"], ["read"])
        self.assertFalse(kiro["includeMcpJson"])
        self.assertFalse(kiro["includePowers"])
        self.assertTrue(all(rule["effect"] == "deny" for rule in kiro["permissions"]["rules"]))
        cursor, _ = parse_definition(self.render("cursor", "ide")[0].content, NATIVE_PATHS["cursor", "ide"])
        self.assertTrue(cursor["readonly"])

    def test_frontmatter_and_toml_injection_remain_literal_user_text(self):
        description = 'Review: "quoted" # yaml &anchor [list] {object} é'
        instructions = 'First line\r\n---\r\nmodel = "injected"\r\n[features]\r\nshell_tool = true\r\n"""\r\n最後'
        normalized = instructions.replace("\r\n", "\n")
        for (product, surface), path in NATIVE_PATHS.items():
            with self.subTest(product=product, surface=surface):
                artifact = self.render(product, surface, description=description, instructions=instructions)[0]
                parsed, body = parse_definition(artifact.content, path)
                self.assertEqual(parsed["description"], description)
                if product == "codex":
                    self.assertEqual(parsed["developer_instructions"], normalized)
                    self.assertFalse(parsed["features"]["shell_tool"])
                elif product == "kiro":
                    self.assertEqual(parsed["prompt"], normalized)
                else:
                    self.assertEqual(body, normalized + "\n")
                    self.assertNotEqual(parsed.get("model"), "injected")

    def test_explicit_effort_needs_both_host_and_model_support(self):
        artifacts = self.render(model_id="gpt-6.1-sol", provider_id="openai", effort="high")
        parsed = tomllib.loads(artifacts[0].content.decode())
        self.assertEqual(parsed["model_reasoning_effort"], "high")
        self.assertEqual(parsed["model"], "gpt-6.1-sol")
        self.assertIn("does not change the parent's provider", artifacts[1].content.decode())
        for options in (
            dict(effort="high"), dict(model_id="inherit", effort="high"),
            dict(model_id="gpt-6.1-sol", effort="high"),
            dict(model_id="unlisted-model", provider_id="openai", effort="high"),
            dict(model_id="gpt-6.1-sol", provider_id="openai", effort="ultra"),
        ):
            with self.subTest(options=options), self.assertRaises(WeaverError):
                self.render(**options)
        for product, surface in (("kimi-code", "cli"), ("qwen-code", "cli"), ("copilot", "vscode"),
                                 ("kilo", "cli"), ("cursor", "ide"), ("kiro", "ide")):
            with self.subTest(product=product), self.assertRaises(WeaverError):
                self.render(product, surface, model_id="gpt-6.1-sol", provider_id="openai", effort="high")
        for options in (dict(effort="high"), dict(model_id="inherit", effort="high"),
                        dict(model_id="gpt-6.1-sol", provider_id="openai", effort="max")):
            with self.subTest(factory=options), self.assertRaises(WeaverError):
                self.render("factory", **options)

    def test_model_controls_preserve_host_semantics(self):
        for product, surface in (("kimi-code", "cli"), ("qoder", "ide")):
            with self.subTest(product=product), self.assertRaises(WeaverError):
                self.render(product, surface, model_id="a-model")
        for product in ("opencode", "kilo"):
            with self.subTest(product=product):
                with self.assertRaises(WeaverError):
                    self.render(product, model_id="gpt-6.1-sol")
                artifact = self.render(product, model_id="gpt-6.1-sol", provider_id="openai")[0]
                parsed, _ = parse_definition(artifact.content, NATIVE_PATHS[product, "cli"])
                self.assertEqual(parsed["model"], "openai/gpt-6.1-sol")
                already_qualified = self.render(product, model_id="openai/gpt-6.1-sol", provider_id="openai")[0]
                qualified, _ = parse_definition(already_qualified.content, NATIVE_PATHS[product, "cli"])
                self.assertEqual(qualified["model"], "openai/gpt-6.1-sol")
        tier = self.render("antigravity", "ide", model_id="flash")[0]
        parsed, _ = parse_definition(tier.content, NATIVE_PATHS["antigravity", "ide"])
        self.assertEqual(parsed["model"], "flash")
        with self.assertRaises(WeaverError):
            self.render("antigravity", "ide", model_id="models/gemini-3.8-flash")

    def test_selected_model_output_modalities_gate_native_language_agent_definitions(self):
        identifier = "fixture/selected-model"
        for product in ("codex", "opencode"):
            for outputs in (["image"], ["embeddings"], ["audio", "video"]):
                snapshot = {"provider_id": "openrouter", "models": [{"id": identifier,
                    "architecture": {"input_modalities": ["text"], "output_modalities": outputs}}]}
                with self.subTest(product=product, outputs=outputs), self.assertRaises(WeaverError) as raised:
                    self.render(product, model_id=identifier, provider_id="openrouter", snapshot=snapshot)
                self.assertIn("does not declare text output", str(raised.exception))
                self.assertNotIn(identifier, str(raised.exception))
            for architecture in (None, {"input_modalities": ["text"]}, {"output_modalities": []},
                                 {"output_modalities": ["text"]}, {"output_modalities": ["text", "image"]}):
                entry = {"id": identifier}
                if architecture is not None:
                    entry["architecture"] = architecture
                snapshot = {"provider_id": "openrouter", "models": [entry]}
                with self.subTest(product=product, architecture=architecture):
                    artifacts = self.render(product, model_id=identifier, provider_id="openrouter", snapshot=snapshot)
                    self.assertEqual(artifacts[0].kind, "agent-definition")
                    fields, _ = parse_definition(artifacts[0].content, NATIVE_PATHS[product, "cli"])
                    expected = identifier if product == "codex" else "openrouter/" + identifier
                    self.assertEqual(fields["model"], expected)
            # Another model's known non-text output cannot classify an unlisted ID.
            snapshot = {"provider_id": "openrouter", "models": [{"id": "fixture/image-only",
                "architecture": {"output_modalities": ["image"]}}]}
            artifacts = self.render(product, model_id=identifier, provider_id="openrouter", snapshot=snapshot)
            self.assertEqual(artifacts[0].kind, "agent-definition")

    def test_devin_desktop_binding_uses_profile_tool_restrictions_and_router_default(self):
        artifacts = self.render("devin", "desktop-local")
        fields, body = parse_definition(artifacts[0].content, NATIVE_PATHS["devin", "desktop-local"])
        self.assertEqual(fields, {"name": "reviewer", "description": "Review small changes.",
                                  "allowed-tools": ["read", "grep", "glob"]})
        self.assertIn("Inspect the requested code", body)
        handoff = artifacts[1].content.decode()
        self.assertIn("Subagents (Preview)", handoff)
        self.assertIn("experimental", handoff)
        self.assertIn("does not request the parent model", handoff)
        self.assertIn("does not change those settings", handoff)
        self.assertNotIn("The default model and reasoning are inherited", handoff)
        explicit = self.render("devin", "desktop-local", model_id="sonnet")[0]
        selected, _ = parse_definition(explicit.content, NATIVE_PATHS["devin", "desktop-local"])
        self.assertEqual(selected["model"], "sonnet")
        for options in (dict(model_id="inherit"), dict(effort="high"),
                        dict(model_id="sonnet", provider_id="anthropic", effort="high"),
                        dict(name="subagent_explore"), dict(name="subagent_general")):
            with self.subTest(options=options), self.assertRaises(WeaverError):
                self.render("devin", "desktop-local", **options)
        row = next(row for row in agent_inventory(ROOT)["definitions"]
                   if (row["product_id"], row["surface_id"]) == ("devin", "desktop-local"))
        self.assertIn("subagent_explore", row["reserved_names"])
        self.assertIn("https://docs.devin.ai/desktop/devin-local", row["evidence"]["sources"])
        self.assertIn("https://docs.devin.ai/cli/subagents", row["evidence"]["sources"])

    def test_openhands_cli_pinned_format_inherits_without_external_tools_or_ignored_overrides(self):
        artifacts = self.render("openhands")
        fields, body = parse_definition(artifacts[0].content, NATIVE_PATHS["openhands", "cli"])
        self.assertEqual(fields, {"name": "reviewer", "description": "Review small changes.",
                                  "model": "inherit", "tools": []})
        self.assertIn("Inspect the requested code", body)
        self.assertEqual(artifacts, self.render("openhands", model_id="inherit"))
        text = artifacts[1].content.decode()
        for caveat in ("CLI 1.16.0", "SDK 1.28.1", "not installed-client", "Only top-level",
                       ".agents/agents/*.md before .openhands/agents/*.md", "Base finish/think",
                       "named local LLM profile, not a provider model ID", "does not establish an OS sandbox"):
            self.assertIn(caveat, text)
        for options in (dict(model_id="fixture/raw-model"), dict(model_id="local-profile"),
                        dict(provider_id="openrouter"), dict(model_id="inherit", provider_id="openrouter"),
                        dict(effort="high"), dict(snapshot={"provider_id": "openrouter", "models": []})):
            with self.subTest(options=options), self.assertRaises(WeaverError):
                self.render("openhands", **options)
        for name in ("readme", "general-purpose", "code-explorer", "bash-runner", "web-researcher"):
            with self.subTest(name=name), self.assertRaises(WeaverError):
                self.render("openhands", name=name)
        row = next(row for row in agent_inventory(ROOT)["definitions"] if row["product_id"] == "openhands")
        sources = row["evidence"]["sources"]
        self.assertTrue(any("283c6ac3610f4299f405f7389f65766dcb83e12a" in source for source in sources))
        self.assertTrue(any("edaac806d1599a4ee1662fd4008fbcd7b537ecd1" in source for source in sources))
        self.assertEqual(row["evidence"]["runtime_checks"], [])
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            destination = Path(directory).resolve()
            write_artifacts(destination, artifacts)
            write_artifacts(destination, artifacts, force=True)
            target = destination / artifacts[0].relative_path
            target.write_text("user edited definition\n", encoding="utf-8")
            with self.assertRaises(WeaverError):
                write_artifacts(destination, artifacts, force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "user edited definition\n")

    def test_openhands_explicit_local_profile_preserves_sdk_reference_as_yaml_data(self):
        for reference in ("Research.v2_profile-1.json", "Inherit", "inherit.json", "con", "NUL.json",
                          "a" * 64, "a" * 64 + ".json"):
            with self.subTest(reference=reference):
                artifacts = self.render("openhands", llm_profile=reference)
                fields, body = parse_definition(artifacts[0].content, NATIVE_PATHS["openhands", "cli"])
                self.assertEqual(fields, {"name": "reviewer", "description": "Review small changes.",
                                          "model": reference, "tools": []})
                self.assertIn("Inspect the requested code", body)
                handoff = artifacts[1].content.decode()
                self.assertIn(f"Explicit local LLM profile reference: {reference}", handoff)
                self.assertNotIn("Explicit native model selection:", handoff)
                self.assertNotIn("Model validation provider:", handoff)
                self.assertIn("profile availability and reasoning behavior remain unknown", handoff)
                self.assertIn("without reading its configuration or credentials", handoff)
        row = next(row for row in agent_inventory(ROOT)["definitions"] if row["product_id"] == "openhands")
        self.assertEqual((row["model_style"], row["model_field"]), ("local-profile", "model"))
        self.assertTrue(any("llm_profile_store.py#L29-L102" in source for source in row["evidence"]["sources"]))

    def test_local_profile_rejects_conflicts_other_hosts_paths_and_secretlike_values(self):
        for options in (dict(model_id="inherit"), dict(model_id="fixture/raw-model"),
                        dict(provider_id="openrouter"), dict(effort="high"), dict(snapshot={})):
            with self.subTest(options=options), self.assertRaisesRegex(WeaverError, "conflicts"):
                self.render("openhands", llm_profile="fixture-profile", **options)
        for product, surface in (("codex", "cli"), ("hermes", "cli"), ("perplexity", "agent-api")):
            with self.subTest(product=product), self.assertRaisesRegex(WeaverError, "only for OpenHands CLI"):
                self.render(product, surface, llm_profile="fixture-profile")
        for reference in ("", [], "inherit", "../profile", ".profile", "/profile", "C:/profile",
                          "C:\\profile", "a/profile", "a\\profile", "$PROFILE", "%PROFILE%", "two words",
                          "a\nmodel: other", 'a"', "a;command", "a" * 65, "a" * 65 + ".json",
                          "sk-proj-synthetic-secret", "BearerSyntheticToken", "github_pat_synthetic-token",
                          "AKIA" + "0" * 16):
            with self.subTest(reference=reference), self.assertRaises(WeaverError) as raised:
                self.render("openhands", llm_profile=reference)
            if isinstance(reference, str) and reference:
                self.assertNotIn(reference, str(raised.exception))

    def test_local_profile_export_never_reads_or_creates_ambient_profiles_or_validates_provider(self):
        original_open = Path.open
        allowed = {ROOT / "catalogs/integrations.json", ROOT / "catalogs/custom-agents.json"}
        reads = []

        def catalog_only_open(path, *args, **kwargs):
            self.assertIn(path, allowed, "profile or other ambient file inspected")
            self.assertEqual(args[0] if args else kwargs.get("mode", "r"), "r")
            reads.append(path)
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", catalog_only_open), \
                mock.patch.object(Path, "home", side_effect=AssertionError("ambient home inspected")), \
                mock.patch.object(Path, "glob", side_effect=AssertionError("profile store listed")), \
                mock.patch("os.getenv", side_effect=AssertionError("ambient environment inspected")), \
                mock.patch("agentweaver.agents.check_model", side_effect=AssertionError("provider model validated")), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("process used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")):
            first = self.render("openhands", llm_profile="unverified-fixture-profile.json")
            self.assertEqual(first, self.render("openhands", llm_profile="unverified-fixture-profile.json"))
        self.assertEqual(set(reads), allowed)

    def test_perplexity_profile_handoff_has_reference_schema_without_native_export(self):
        artifacts = self.render("perplexity", "agent-api")
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].kind, "agent-handoff")
        text = artifacts[0].content.decode()
        self.assertIn("Profiles > Create profile", text)
        self.assertIn("POST /v1/agent", text)
        example = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(example["profile"], {
            "type": "custom", "id": "profile_REPLACE_WITH_YOUR_PROFILE_ID",
            "version": "REPLACE_WITH_SAVED_VERSION",
        })
        self.assertEqual(set(example), {"profile", "input"})
        self.assertIn("either profile or effort, not both", text)
        self.assertIn("tools merge per tool", text)
        self.assertIn("not an authorization boundary", text)
        self.assertIn("not a complete Profile export or upload schema", text)
        self.assertIn("does not revoke ambient", text)
        self.assertIn("Inspect the requested code", text)
        for options in (dict(model_id="a-model"), dict(effort="high"), dict(provider_id="openai")):
            with self.subTest(options=options), self.assertRaises(WeaverError):
                self.render("perplexity", "agent-api", **options)

    def test_reviewed_requested_gaps_are_bounded_unknowns_with_first_party_sources(self):
        expected = {("perplexity", "app"), ("perplexity", "orchestrator"),
                    ("lm-studio", "desktop"), ("lm-studio", "bionic"), ("lm-studio", "api")}
        rows = {(row["product_id"], row["surface_id"]): row for row in agent_inventory(ROOT)["definitions"]}
        for key in expected:
            with self.subTest(product=key[0], surface=key[1]):
                row = rows[key]
                self.assertEqual(row["state"], "unknown")
                self.assertIsNone(row["format"])
                self.assertIsNone(row["path_template"])
                self.assertTrue(row["evidence"]["sources"])
                self.assertEqual(row["evidence"]["checked_on"], "2026-10-26")
                self.assertEqual(row["evidence"]["runtime_checks"], [])

    def test_manual_routes_produce_only_labeled_handoff_and_reject_ignored_overrides(self):
        for product, surface in (("hermes", "cli"), ("jetbrains-air", "desktop"), ("zed", "ide"),
                                 *(("chatgpt", sid) for sid in ("desktop", "web", "work-local", "work-cloud"))):
            with self.subTest(product=product, surface=surface):
                artifacts = self.render(product, surface)
                self.assertEqual(len(artifacts), 1)
                self.assertEqual(artifacts[0].kind, "agent-handoff")
                self.assertIn("not a discoverable subagent file", artifacts[0].content.decode())
                self.assertIn("Inspect the requested code", artifacts[0].content.decode())
                with self.assertRaises(WeaverError):
                    self.render(product, surface, model_id="gpt-6.1-sol")

    def test_unknown_and_unsupported_routes_fail_closed(self):
        for row in agent_inventory(ROOT)["definitions"]:
            if row["state"] in {"unknown", "unsupported"}:
                with self.subTest(product=row["product_id"], surface=row["surface_id"]), self.assertRaises(WeaverError):
                    self.render(row["product_id"], row["surface_id"])
        with self.assertRaises(WeaverError):
            self.render(surface="cloud")

    def test_untrusted_input_names_controls_and_metadata_are_rejected(self):
        for name in ("../outside", "UPPER", "with_under", "a/b", "a\\b", "con", "nul", "-name", "name-", "x..y", "", "worker"):
            with self.subTest(name=name), self.assertRaises(WeaverError):
                self.render(name=name)
        for field, value in (("description", "line\nmodel: attacker"), ("description", "bad\ud800"),
                             ("instructions", "line\rbad"), ("instructions", "bad\x00"),
                             ("instructions", "bad\u202e"), ("model_id", "sk-do-not-stage-this-value"),
                             ("model_id", []), ("effort", {}), ("snapshot", [])):
            with self.subTest(field=field, value=repr(value)), self.assertRaises(WeaverError):
                self.render(**{field: value})

    def test_model_validation_without_provider_cannot_emit_paths_or_credentials(self):
        for model in (
            "C:/private-fixture/model", "c:/private-fixture/model", "/private-fixture/model",
            "provider//model", "provider/../model", "provider/./model", "provider/model/",
            "sk-proj-synthetic-secret", "SK-PROJ-synthetic-secret", "BearerSyntheticToken",
            "provider/sk-proj-synthetic-secret", "provider/ghp_" + "x" * 24,
        ):
            with self.subTest(model=model), self.assertRaises(WeaverError) as raised:
                self.render(model_id=model)
            self.assertNotIn(model, str(raised.exception))
        # A provider is optional for a syntactically valid inherited-provider model.
        artifact = self.render(model_id="valid-future-model")[0]
        self.assertEqual(tomllib.loads(artifact.content.decode())["model"], "valid-future-model")

    def test_catalog_does_not_accept_guessed_formats_paths_or_product_references(self):
        original = json.loads((ROOT / "catalogs/custom-agents.json").read_text())
        native_index = next(index for index, row in enumerate(original["definitions"]) if row["state"] == "native")
        for field, value in (("path_template", "../../{name}.md"), ("format", "guessed-yaml"),
                             ("product_id", "not-a-product"), ("surface_id", "not-a-surface"),
                             ("state", []), ("format", []), ("model_style", []),
                             ("model_style", "local-profile"),
                             ("model_field", "approval_policy"), ("effort_field", "permissionMode"),
                             ("extra", "unknown")):
            catalog = copy.deepcopy(original)
            catalog["definitions"][native_index][field] = value
            with self.subTest(field=field), mock.patch("agentweaver.agents.load_json", return_value=catalog), self.assertRaises(WeaverError):
                agent_inventory(ROOT)
        openhands = next(row for row in original["definitions"] if row["product_id"] == "openhands")
        openhands["model_style"] = "bare"
        with mock.patch("agentweaver.agents.load_json", return_value=original), self.assertRaises(WeaverError):
            agent_inventory(ROOT)

    def test_rendering_is_deterministic_offline_and_uses_the_safe_staged_writer(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("process used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")), \
                mock.patch.object(Path, "home", side_effect=AssertionError("ambient home inspected")):
            first = (self.render() + self.render("devin", "desktop-local") + self.render("perplexity", "agent-api") +
                     self.render("openhands"))
            self.assertEqual(first, self.render() + self.render("devin", "desktop-local") +
                             self.render("perplexity", "agent-api") + self.render("openhands"))
            self.assertTrue(agent_inventory(ROOT)["definitions"])
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            destination = Path(directory).resolve()
            write_artifacts(destination, first)
            for artifact in first:
                self.assertEqual((destination / artifact.relative_path).read_bytes(), artifact.content)
            with self.assertRaises(WeaverError):
                write_artifacts(destination, first)


if __name__ == "__main__":
    unittest.main()
