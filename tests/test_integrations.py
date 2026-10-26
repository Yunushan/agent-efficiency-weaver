import copy
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from agentweaver.adapters import render_mcp
from agentweaver.config import WeaverError, load_json
from agentweaver.integrations import (
    get_integration,
    get_surface,
    load_integrations,
    load_operating_systems,
)
from agentweaver.providers import provider_evidence
from agentweaver.skills import render_instruction_bundle

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_IDS = {
    "kilo",
    "opencode",
    "cline",
    "copilot",
    "cursor",
    "openhands",
    "zed",
    "junie",
    "goose",
    "kiro",
    "antigravity",
    "pi",
    "codex",
    "devin",
    "abacus",
    "qoder",
    "amp",
    "claude-code",
    "aider",
    "openrouter",
}

EXPECTED_DEFAULT_SURFACES = {
    "kilo": "cli",
    "opencode": "cli",
    "cline": "ide",
    "copilot": "vscode",
    "cursor": "ide",
    "openhands": "cli",
    "zed": "ide",
    "junie": "cli",
    "goose": "cli",
    "kiro": "ide",
    "antigravity": "ide",
    "pi": "cli",
    "codex": "cli",
    "devin": "desktop-local",
    "abacus": "desktop",
    "qoder": "cli",
    "amp": "cli",
    "claude-code": "cli",
    "aider": "cli",
    "openrouter": "gateway",
}

EXPECTED_NATIVE_PATHS = {
    "kilo": ".kilo/kilo.json",
    "opencode": "opencode.json",
    "cline": None,
    "copilot": ".vscode/mcp.json",
    "cursor": ".cursor/mcp.json",
    "openhands": None,
    "zed": None,
    "junie": ".junie/mcp/mcp.json",
    "goose": None,
    "kiro": ".kiro/settings/mcp.json",
    "antigravity": ".agents/mcp_config.json",
    "pi": ".pi/mcp.json",
    "codex": ".codex/config.toml",
    "devin": ".devin/mcp_config.json",
    "abacus": ".abacusai/mcp.json",
    "qoder": ".qoder/settings.json",
    "amp": ".amp/settings.json",
    "claude-code": ".mcp.json",
    "aider": None,
    "openrouter": None,
}

EXPECTED_DIALECTS = {
    "kilo": "mcp.type-remote",
    "opencode": "mcp.type-remote",
    "cline": "mcpServers.type-streamableHttp",
    "copilot": "servers.type-http",
    "cursor": "mcpServers.url",
    "openhands": "mcpServers.url",
    "zed": "context_servers.url",
    "junie": "mcpServers.type-http",
    "goose": "extensions.uri-streamable_http",
    "kiro": "mcpServers.url",
    "antigravity": "mcpServers.serverUrl",
    "pi": "mcpServers.url-or-type-http",
    "codex": "mcp_servers.toml-url",
    "devin": "mcpServers.transport-http",
    "abacus": "mcpServers.url",
    "qoder": "mcpServers.type-http",
    "amp": "amp.mcpServers.url",
    "claude-code": "mcpServers.type-http",
    "aider": None,
    "openrouter": None,
}

CAPABILITY_NAMES = {"mcp", "instructions", "skills", "provider", "os"}
CAPABILITY_STATES = {"available", "manual", "unsupported", "unknown"}
OS_IDS = {"windows", "macos", "linux", "bsd", "solaris", "android", "ios"}

ADAPTER_SURFACES = {
    ("kilo", "cli"),
    ("kilo", "ide"),
    ("opencode", "cli"),
    ("cline", "ide"),
    ("copilot", "vscode"),
    ("cursor", "ide"),
    ("openhands", "cli"),
    ("zed", "ide"),
    ("junie", "cli"),
    ("goose", "cli"),
    ("kiro", "ide"),
    ("antigravity", "ide"),
    ("pi", "cli"),
    ("codex", "cli"),
    ("devin", "desktop-local"),
    ("abacus", "desktop"),
    ("qoder", "cli"),
    ("qoder", "ide"),
    ("amp", "cli"),
    ("claude-code", "cli"),
}

INSTRUCTION_SURFACES = ADAPTER_SURFACES | {("aider", "cli")}
SKILL_SURFACES = INSTRUCTION_SURFACES

PROVIDER_ROUTES = {
    ("kilo", "cli"): "manual",
    ("kilo", "ide"): "manual",
    ("opencode", "cli"): "manual",
    ("cline", "ide"): "manual",
    ("copilot", "vscode"): "manual",
    ("cursor", "ide"): "manual",
    ("openhands", "cli"): "manual",
    ("zed", "ide"): "manual",
    ("junie", "cli"): "manual",
    ("goose", "cli"): "manual",
    ("pi", "cli"): "manual",
    ("codex", "cli"): "available",
    ("codex", "desktop"): "available",
    ("codex", "ide"): "available",
    ("chatgpt", "desktop"): "manual",
    ("chatgpt", "work-local"): "manual",
    ("factory", "cli"): "manual",
    ("hermes", "cli"): "manual",
    ("qwen-code", "cli"): "manual",
    ("kimi-code", "cli"): "manual",
    ("abacus", "desktop"): "manual",
    ("qoder", "ide"): "manual",
    ("amp", "cli"): "manual",
    ("claude-code", "cli"): "manual",
    ("aider", "cli"): "manual",
}

PROVIDER_GAP_SOURCES = {
    ("kiro", "ide"): "https://kiro.dev/docs/models/",
    ("antigravity", "ide"): "https://antigravity.google/docs/models",
    ("devin", "desktop-local"): "https://docs.devin.ai/desktop/models",
    ("qoder", "cli"): "https://docs.qoder.com/cli/custom-models",
    ("chatgpt", "web"): "https://learn.chatgpt.com/docs/auth",
    ("chatgpt", "work-cloud"): "https://learn.chatgpt.com/docs/auth",
    ("lm-studio", "desktop"): "https://lmstudio.ai/lmstudio/openai-compat-endpoint",
    ("lm-studio", "bionic"): "https://lmstudio.ai/docs/bionic",
    ("perplexity", "app"): "https://www.perplexity.ai/help-center/en/articles/20260917-effort-mode-in-perplexity-computer",
}
PROVIDER_GAPS = set(PROVIDER_GAP_SOURCES)



class IntegrationRegistryTests(unittest.TestCase):
    def setUp(self):
        self.integrations = load_integrations(ROOT)

    def test_exact_inventory_and_identity(self):
        catalog = load_json(ROOT / "catalogs/integrations.json")
        self.assertEqual(set(self.integrations), {item["id"] for item in catalog["integrations"]})
        self.assertTrue(EXPECTED_IDS <= set(self.integrations))
        self.assertEqual(len({item.name.casefold() for item in self.integrations.values()}), len(self.integrations))
        self.assertEqual(
            {item.id for item in self.integrations.values()},
            set(self.integrations),
        )
        self.assertEqual(
            [item.id for item in self.integrations.values() if item.kind == "agent"],
            sorted(item.id for item in self.integrations.values() if item.kind == "agent"),
        )

    def test_kinds_and_defaults(self):
        for identifier in EXPECTED_IDS:
            item = self.integrations[identifier]
            self.assertEqual(item.kind, "provider" if identifier == "openrouter" else "agent")
            self.assertEqual(item.default_surface, EXPECTED_DEFAULT_SURFACES[identifier])
        for item in self.integrations.values():
            self.assertIn(item.kind, {"agent", "provider", "hybrid"})
            self.assertIn(item.default_surface, item.surface_ids())

    def test_native_paths_and_dialects(self):
        for integration_id, expected_path in EXPECTED_NATIVE_PATHS.items():
            surface = self.integrations[integration_id].surface(
                EXPECTED_DEFAULT_SURFACES[integration_id]
            )
            self.assertEqual(surface.native_path_hint, expected_path)
            self.assertEqual(
                surface.mcp_dialect,
                EXPECTED_DIALECTS[integration_id],
                integration_id,
            )

    def test_all_surfaces_have_required_metadata_and_evidence(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                self.assertTrue(surface.id)
                self.assertIn(surface.config_scope, {"project", "user", "app", "request", "none"})
                self.assertTrue(surface.instruction_mode)
                self.assertEqual(set(surface.os_workflows), OS_IDS)
                self.assertEqual(set(surface.capabilities), CAPABILITY_NAMES)
                for name, evidence in surface.capabilities.items():
                    self.assertIn(evidence.state, CAPABILITY_STATES, (integration.id, surface.id, name))
                    self.assertEqual(evidence.runtime_checks, ())
                    if evidence.state in {"available", "manual", "unsupported"}:
                        self.assertTrue(evidence.sources, (integration.id, surface.id, name))
                        self.assertIsNotNone(evidence.checked_on, (integration.id, surface.id, name))

    def test_safe_relative_artifact_paths(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                path = surface.native_path_hint
                if path is None or surface.config_scope not in {"project", "request"}:
                    continue
                self.assertFalse(path.startswith("/"))
                self.assertNotIn("\\", path)
                self.assertNotIn(":", path)
                self.assertNotIn("~", path)
                parts = path.split("/")
                self.assertNotIn("..", parts)
                self.assertNotIn("", parts)

    def test_surfaces_tuple_and_config_fields(self):
        for integration in self.integrations.values():
            self.assertIsInstance(integration.surfaces, tuple)
            self.assertEqual(
                tuple(surface.id for surface in integration.surfaces),
                integration.surface_ids(),
            )
            for surface in integration.surfaces:
                self.assertIsInstance(surface.config_fields, tuple)
        cline = self.integrations["cline"].surface("ide")
        self.assertEqual(
            cline.config_fields,
            ("mcpServers", "type", "url", "disabled"),
        )
        self.assertIn(
            "https://docs.cline.bot/mcp/configuring-mcp-servers",
            cline.capabilities["mcp"].sources,
        )

    def test_abacus_skills_are_not_exported(self):
        surface = self.integrations["abacus"].surface("desktop")
        self.assertIsNone(surface.skills_path)
        self.assertEqual(surface.capabilities["skills"].state, "manual")

    def test_alternate_surfaces_are_documented(self):
        expected = {"kilo": {"ide"}}
        for integration_id, surface_ids in expected.items():
            actual = set(self.integrations[integration_id].surface_ids())
            self.assertTrue(surface_ids <= actual, integration_id)
            for surface_id in surface_ids:
                surface = self.integrations[integration_id].surface(surface_id)
                self.assertIn(
                    surface.capabilities["mcp"].state,
                    CAPABILITY_STATES,
                )
                self.assertIsNotNone(surface.capabilities["mcp"].checked_on)

    def test_os_workflows_avoid_unverified_claims(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                self.assertEqual(surface.os_workflows["bsd"], "unknown")
                self.assertEqual(surface.os_workflows["solaris"], "unknown")
        self.assertEqual(
            self.integrations["aider"].surface("cli").os_workflows["windows"],
            "unknown",
        )
        pi = self.integrations["pi"].surface("cli").os_workflows
        self.assertEqual(pi["windows"], "available")
        self.assertEqual(pi["android"], "manual")
        self.assertEqual(
            self.integrations["cursor"].surface("ide").os_workflows["ios"],
            "manual",
        )
        kilo_cli = self.integrations["kilo"].surface("cli")
        self.assertEqual(kilo_cli.capabilities["os"].state, "available")
        self.assertEqual(kilo_cli.capabilities["os"].sources,
                         ("https://kilo.ai/docs/code-with-ai/platforms/cli",))
        self.assertEqual(kilo_cli.capabilities["os"].checked_on, "2026-10-26")
        self.assertIn("Kilo CLI >=1.0", kilo_cli.capabilities["os"].version_constraint)
        self.assertEqual(kilo_cli.capabilities["os"].runtime_checks, ())
        for os_id in ("windows", "macos", "linux"):
            self.assertEqual(kilo_cli.os_workflows[os_id], "available")
        self.assertEqual(
            self.integrations["aider"].surface("cli").capabilities["os"].state,
            "unknown",
        )
        pi_os = self.integrations["pi"].surface("cli").capabilities["os"]
        self.assertEqual(pi_os.state, "manual")
        self.assertTrue(all("pi.dev" in source for source in pi_os.sources))
        cursor_os = self.integrations["cursor"].surface("ide").capabilities["os"]
        self.assertEqual(cursor_os.state, "available")
        self.assertTrue(all("cursor.com" in source for source in cursor_os.sources))

    def test_three_primary_os_routes_keep_exact_sources_constraints_and_runtime_limits(self):
        expected = {
            ("cursor", "ide"): (
                ("https://cursor.com/docs/get-started/quickstart", "https://cursor.com/download",
                 "https://cursor.com/docs/cloud-agent/mobile"),
                ("3.27", "macOS >=12", "Windows >=10", "x64/ARM64", "minima unestablished",
                 "iOS >=26 is remote control", "Android planned"),
            ),
            ("claude-code", "cli"): (
                ("https://code.claude.com/docs/en/setup", "https://code.claude.com/docs/en/changelog.md",
                 "https://code.claude.com/docs/en/mobile"),
                ("2.1.310", "macOS >=13", "Windows >=10 1809", "Server >=2019", "Ubuntu >=20.04",
                 "Debian >=10", "Alpine >=3.19", "x64/ARM64", "Native Windows has no sandbox",
                 "WSL2 supports sandbox, WSL1 does not", "Mobile remote clients only"),
            ),
            ("qoder", "cli"): (
                ("https://docs.qoder.com/cli/installation", "https://docs.qoder.com/release-notes/qoder-cli",
                 "https://docs.qoder.com/mobile/app/remote-control"),
                ("1.1.78", "macOS/Linux arm64 or amd64", "Windows amd64 only (ARM64 unsupported)",
                 "Minimum OS versions", "minima unestablished", "Node >=20 only for legacy npm",
                 "Mobile remote clients only"),
            ),
        }
        for (product_id, surface_id), (sources, constraints) in expected.items():
            with self.subTest(product=product_id, surface=surface_id):
                surface = self.integrations[product_id].surface(surface_id)
                evidence = surface.capabilities["os"]
                self.assertEqual(evidence.state, "available")
                self.assertEqual(evidence.sources, sources)
                self.assertEqual(evidence.checked_on, "2026-10-26")
                for constraint in constraints:
                    self.assertIn(constraint, evidence.version_constraint)
                for os_id in ("windows", "macos", "linux"):
                    self.assertEqual(surface.os_workflows[os_id], "available")
                for os_id in ("bsd", "solaris"):
                    self.assertEqual(surface.os_workflows[os_id], "unknown")
                self.assertEqual(surface.os_workflows["ios"], "manual")
                self.assertEqual(surface.os_workflows["android"], "unknown" if product_id == "cursor" else "manual")
                self.assertEqual(evidence.fixture_tests, ())
                self.assertEqual(evidence.runtime_checks, ())

    def test_openhands_cli_evidence_identifies_consuming_loaders_and_exact_fixtures(self):
        surface = self.integrations["openhands"].surface("cli")
        cli = "https://github.com/OpenHands/OpenHands-CLI/blob/283c6ac3610f4299f405f7389f65766dcb83e12a/"
        sdk = "https://github.com/OpenHands/software-agent-sdk/blob/edaac806d1599a4ee1662fd4008fbcd7b537ecd1/"
        mcp = surface.capabilities["mcp"]
        self.assertEqual(surface.config_scope, "user")
        self.assertIsNone(surface.native_path_hint)
        self.assertEqual(surface.documented_location, "~/.openhands/mcp.json")
        self.assertEqual(surface.config_fields, ("mcpServers", "url", "command", "args"))
        self.assertEqual(mcp.state, "manual")
        self.assertEqual(mcp.sources, (
            "https://github.com/OpenHands/docs/blob/main/openhands/usage/cli/mcp-servers.mdx",
            cli + "openhands_cli/mcp/mcp_utils.py#L15-L58",
            cli + "openhands_cli/mcp/mcp_utils.py#L160-L190",
        ))
        self.assertEqual(mcp.fixture_tests, ("tests/test_adapters.py", "tests/test_stdio_adapters.py"))
        loader_sources = (
            cli + "openhands_cli/stores/agent_store.py#L408-L421",
            sdk + "openhands-sdk/openhands/sdk/skills/skill.py#L853-L923",
            sdk + "openhands-sdk/openhands/sdk/skills/utils.py#L254-L324",
        )
        for capability in ("instructions", "skills"):
            evidence = surface.capabilities[capability]
            self.assertEqual(evidence.state, "available")
            self.assertEqual(evidence.sources, loader_sources)
            self.assertEqual(evidence.fixture_tests, ("tests/test_skills.py",))
            self.assertIn("working directory then Git root", evidence.version_constraint)
            self.assertIn("immediate child directories", evidence.version_constraint)
        for capability in ("mcp", "instructions", "skills"):
            evidence = surface.capabilities[capability]
            self.assertIn("CLI 1.16.0", evidence.version_constraint)
            self.assertIn("SDK 1.28.1", evidence.version_constraint)
            limitation = "not installed-client evidence" if capability == "mcp" else "no installed-client evidence"
            self.assertIn(limitation, evidence.version_constraint)
            self.assertEqual(evidence.checked_on, "2026-10-26")
            self.assertEqual(evidence.runtime_checks, ())

    def test_operating_system_catalog(self):
        systems = load_operating_systems(ROOT)
        self.assertEqual(set(systems), OS_IDS)
        for system in systems.values():
            self.assertEqual(system.family, system.id)
            self.assertTrue(system.name)
            self.assertIn(system.core_runtime, CAPABILITY_STATES)
            self.assertEqual(
                set(system.capabilities),
                {"core_runtime", "native_agent", "manual_remote"},
            )
            self.assertEqual(system.capabilities["core_runtime"].runtime_checks, ())
        self.assertEqual(
            set(systems["bsd"].variants),
            {"freebsd", "openbsd", "netbsd", "dragonflybsd"},
        )
        self.assertIn("illumos", systems["solaris"].variants)
        self.assertEqual(systems["ios"].capabilities["native_agent"].state, "unsupported")

    def test_provider_metadata_matches_product_roles(self):
        provider = self.integrations["openrouter"]
        surface = provider.surface("gateway")
        self.assertEqual(surface.capabilities["mcp"].state, "unsupported")
        self.assertEqual(surface.capabilities["provider"].state, "available")
        self.assertEqual(
            surface.capabilities["provider"].sources,
            ("https://openrouter.ai/docs/quickstart",),
        )
        self.assertEqual(surface.capabilities["provider"].checked_on, "2026-10-22")
        self.assertEqual(surface.provider["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(surface.provider["endpoint"], "/chat/completions")
        self.assertEqual(surface.provider["model_selection"], "explicit")
        self.assertEqual(surface.provider["model_ids"], [])
        self.assertEqual(surface.provider["secret_env"], "OPENROUTER_API_KEY")
        self.assertFalse(surface.provider["mcp_service_enabled"])
        for integration in self.integrations.values():
            metadata = [surface.provider for surface in integration.surfaces]
            if integration.kind == "agent":
                self.assertTrue(all(value is None for value in metadata))
            elif integration.kind == "provider":
                self.assertTrue(all(value is not None for value in metadata))
            else:
                self.assertIn(None, metadata)
                self.assertTrue(any(value is not None for value in metadata))
            for value in metadata:
                if value is not None:
                    self.assertIs(value["mcp_service_enabled"], False)
                    self.assertEqual(value["model_selection"], "explicit")

    def test_documentation_and_runtime_evidence_are_independent(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                for evidence in surface.capabilities.values():
                    self.assertIsInstance(evidence.sources, tuple)
                    self.assertIsInstance(evidence.fixture_tests, tuple)
                    self.assertIsInstance(evidence.runtime_checks, tuple)
                    self.assertEqual(evidence.runtime_checks, ())
                    if evidence.state != "unknown":
                        self.assertRegex(evidence.checked_on, r"^\d{4}-\d{2}-\d{2}$")
                    for reference in (*evidence.fixture_tests, *evidence.runtime_checks):
                        self.assertTrue((ROOT / reference).is_file(), reference)

    def test_mcp_fixtures_cover_only_exported_surfaces(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                if integration.id not in EXPECTED_IDS or surface.id not in {
                    item[1] for item in ADAPTER_SURFACES | INSTRUCTION_SURFACES | PROVIDER_GAPS
                    if item[0] == integration.id
                }:
                    continue
                expected = (
                    ("tests/test_capability_expansion.py",) if (integration.id, surface.id) == ("qoder", "ide") else
                    ("tests/test_adapters.py", "tests/test_stdio_adapters.py")
                    if (integration.id, surface.id) == ("openhands", "cli") else
                    ("tests/test_adapters.py",)
                    if (integration.id, surface.id) in ADAPTER_SURFACES
                    else ()
                )
                self.assertEqual(
                    surface.capabilities["mcp"].fixture_tests,
                    expected,
                    (integration.id, surface.id),
                )
                self.assertEqual(surface.capabilities["mcp"].runtime_checks, ())

    def test_instruction_and_skill_fixtures_track_documented_routes(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                key = (integration.id, surface.id)
                if integration.id not in EXPECTED_IDS or key not in INSTRUCTION_SURFACES | PROVIDER_GAPS | {("qoder", "ide"), ("openrouter", "gateway")}:
                    continue
                expected_instructions = (
                    ("tests/test_skills.py",) if key in INSTRUCTION_SURFACES else ()
                )
                expected_skills = (
                    ("tests/test_skills.py",) if key in SKILL_SURFACES else ()
                )
                if key == ("qoder", "ide"):
                    expected_instructions = ("tests/test_skills.py", "tests/test_capability_expansion.py")
                if key in {("cline", "ide"), ("cursor", "ide"), ("qoder", "ide")}:
                    expected_skills = ("tests/test_skills.py", "tests/test_capability_expansion.py")
                self.assertEqual(
                    surface.capabilities["instructions"].fixture_tests,
                    expected_instructions,
                    key,
                )
                self.assertEqual(
                    surface.capabilities["skills"].fixture_tests,
                    expected_skills,
                    key,
                )

    def test_provider_capabilities_match_documented_provider_evidence(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                key = (integration.id, surface.id)
                if key not in set(PROVIDER_ROUTES) | PROVIDER_GAPS:
                    continue
                evidence = surface.capabilities["provider"]
                if key in PROVIDER_ROUTES:
                    documented = provider_evidence(integration.id, surface.id)
                    self.assertEqual(evidence.state, PROVIDER_ROUTES[key], key)
                    self.assertEqual(evidence.sources, tuple(documented["sources"]), key)
                    self.assertEqual(evidence.checked_on, documented["checked_on"], key)
                    self.assertEqual(
                        evidence.fixture_tests,
                        tuple(documented["fixture_tests"]),
                        key,
                    )
                else:
                    self.assertIn(key, PROVIDER_GAPS, key)
                    self.assertEqual(evidence.state, "unknown", key)
                    self.assertIn(PROVIDER_GAP_SOURCES[key], evidence.sources, key)
                    self.assertEqual(evidence.checked_on, "2026-10-26", key)
                    self.assertEqual(evidence.fixture_tests, (), key)
                    with self.assertRaises(WeaverError):
                        provider_evidence(integration.id, surface.id)
                self.assertIsNone(evidence.version_constraint, key)
                self.assertEqual(evidence.runtime_checks, (), key)

    def test_qoder_ide_has_documented_app_mcp_and_native_instruction_routes(self):
        qoder = self.integrations["qoder"]
        self.assertEqual(qoder.default_surface, "cli")
        self.assertEqual(qoder.surface_ids(), ("cli", "ide"))
        cli = qoder.surface("cli")
        self.assertEqual(cli.native_path_hint, ".qoder/settings.json")
        self.assertEqual(cli.mcp_dialect, "mcpServers.type-http")
        self.assertEqual(cli.capabilities["provider"].state, "unknown")
        self.assertEqual(cli.capabilities["provider"].sources, (
            "https://docs.qoder.com/cli/custom-models", "https://docs.qoder.com/cli/model"))

        ide = qoder.surface("ide")
        self.assertEqual(ide.mcp_dialect, "mcpServers.type-sse")
        self.assertEqual(ide.config_fields, ("mcpServers", "type", "url", "command", "args"))
        self.assertEqual(ide.config_scope, "app")
        self.assertIsNone(ide.native_path_hint)
        self.assertEqual(ide.documented_location,
                         "Qoder IDE Settings > MCP > My Servers > Add; JSON editor. Persistent configuration path unestablished.")
        self.assertEqual(ide.instruction_mode, "rules")
        self.assertEqual(ide.instruction_paths, ("AGENTS.md", ".qoder/rules/"))
        self.assertEqual(ide.skills_path, ".qoder/skills")
        self.assertEqual(ide.os_workflows, {os_id: "available" if os_id in {"windows", "macos", "linux"}
                                          else "unknown" for os_id in OS_IDS})
        for name in ("mcp", "instructions", "skills", "os"):
            evidence = ide.capabilities[name]
            self.assertEqual(evidence.state, "manual" if name == "mcp" else "available", name)
            self.assertTrue(evidence.sources, name)
            self.assertEqual(evidence.checked_on, "2026-10-26", name)
            self.assertEqual(evidence.runtime_checks, (), name)
            if name != "os":
                self.assertIsNone(evidence.version_constraint, name)
            else:
                self.assertIn("Qoder IDE card only", evidence.version_constraint)
        self.assertEqual(ide.capabilities["mcp"].fixture_tests, ("tests/test_capability_expansion.py",))
        for name in ("instructions", "skills"):
            self.assertEqual(ide.capabilities[name].fixture_tests,
                             ("tests/test_skills.py", "tests/test_capability_expansion.py"))
        provider = ide.capabilities["provider"]
        documented = provider_evidence("qoder", "ide")
        self.assertEqual(provider.state, "manual")
        self.assertEqual(provider.sources, tuple(documented["sources"]))
        self.assertEqual(provider.checked_on, documented["checked_on"])
        self.assertEqual(provider.fixture_tests, ("tests/test_providers.py",))
        self.assertEqual(provider.runtime_checks, ())
        self.assertIsNone(provider.version_constraint)

    def test_qoder_ide_exports_manual_ui_fragment_agents_and_canonical_native_skill(self):
        qoder = self.integrations["qoder"]
        profile = load_json(ROOT / "profiles/efficient.json")
        catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        empty = render_mcp(qoder, "ide", profile, catalog, [])[0]
        self.assertEqual(empty.relative_path.as_posix(), "qoder/fragments/mcp.ide.json")
        self.assertEqual(empty.kind, "mcp-fragment")
        self.assertEqual(json.loads(empty.content), {"mcpServers": {}})
        ide = render_mcp(qoder, "ide", profile, catalog, ["context7"])[0]
        cli = render_mcp(qoder, "cli", profile, catalog, ["context7"])[0]
        self.assertEqual(json.loads(ide.content)["mcpServers"]["context7"]["type"], "sse")
        self.assertEqual(json.loads(cli.content)["mcpServers"]["context7"]["type"], "http")
        artifacts = render_instruction_bundle(qoder, "ide", profile, ["security-review"], ROOT)
        self.assertEqual(artifacts[0].relative_path.as_posix(), "qoder/AGENTS.md")
        self.assertEqual(artifacts[0].kind, "instructions")
        skill = next(artifact for artifact in artifacts if artifact.kind == "skill")
        self.assertEqual(skill.relative_path.as_posix(), "qoder/.qoder/skills/security-review/SKILL.md")
        self.assertEqual(skill.content, (ROOT / ".agents/skills/security-review/SKILL.md").read_bytes().replace(b"\r\n", b"\n"))


    def test_get_integration_and_surface(self):
        self.assertEqual(get_integration("codex", ROOT).id, "codex")
        self.assertEqual(get_surface("codex", "cli", ROOT).id, "cli")
        with self.assertRaises(WeaverError):
            get_integration("missing", ROOT)
        with self.assertRaises(WeaverError):
            get_integration("", ROOT)
        with self.assertRaises(WeaverError):
            get_surface("codex", "missing-surface", ROOT)

    def test_records_are_frozen(self):
        integration = self.integrations["kilo"]
        surface = integration.surface("cli")
        evidence = surface.capabilities["mcp"]
        with self.assertRaises(FrozenInstanceError):
            integration.name = "Changed"
        with self.assertRaises(FrozenInstanceError):
            surface.id = "changed"
        with self.assertRaises(FrozenInstanceError):
            evidence.state = "unknown"

    def test_catalog_extensions_do_not_require_runtime_product_counts(self):
        data = load_json(ROOT / "catalogs/integrations.json")
        entry = copy.deepcopy(data["integrations"][0])
        entry.update(id="future-agent", name="Future Agent")
        data["integrations"].append(entry)
        self.assertIn("future-agent", self._load_copy(data))

    def test_hybrid_roles_require_host_and_provider_surfaces(self):
        data = load_json(ROOT / "catalogs/integrations.json")
        host = copy.deepcopy(next(item for item in data["integrations"] if item["id"] == "abacus"))
        api = copy.deepcopy(next(item for item in data["integrations"] if item["id"] == "openrouter")["surfaces"][0])
        host.update(id="hybrid-fixture", name="Hybrid Fixture", kind="hybrid")
        api["id"] = "api"
        host["surfaces"].append(api)
        data["integrations"] = [host]
        self.assertEqual(self._load_copy(data)["hybrid-fixture"].kind, "hybrid")
        for kind in ("agent", "provider"):
            invalid = copy.deepcopy(data)
            invalid["integrations"][0]["kind"] = kind
            self._assert_invalid(invalid)
        for only in (host["surfaces"][:1], host["surfaces"][1:]):
            invalid = copy.deepcopy(data)
            invalid["integrations"][0]["surfaces"] = only
            invalid["integrations"][0]["default_surface"] = only[0]["id"]
            self._assert_invalid(invalid)

    def test_provider_urls_are_credential_free_and_http_is_local_lm_studio_only(self):
        data = load_json(ROOT / "catalogs/integrations.json")
        provider = copy.deepcopy(next(item for item in data["integrations"] if item["id"] == "openrouter"))
        provider.update(id="lm-studio", name="Local Provider Fixture")
        data["integrations"] = [provider]
        metadata = provider["surfaces"][0]["provider"]
        metadata["secret_env"] = None
        for address in ("https://models.example/v1", "http://localhost:1234/api/v1",
                        "http://127.0.0.1:1234/api/v1", "http://[::1]:1234/api/v1"):
            metadata["base_url"] = address
            self.assertIsNone(self._load_copy(data)["lm-studio"].surfaces[0].provider["secret_env"])
        for address in ("http://remote.example/v1", "http://localhost.example/v1", "http://127.1/v1",
                        "http://[::1", "http://localhost:bad/v1", "http://user:NEVER-ECHO@localhost/v1",
                        "https://user:NEVER-ECHO@models.example/v1", "https://models.example?token=NEVER-ECHO",
                        "https://models.example/#NEVER-ECHO", "https://models.example\\evil"):
            metadata["base_url"] = address
            self._assert_invalid(data)
        metadata["base_url"] = "http://localhost:1234/v1"
        provider["id"] = "other-provider"
        self._assert_invalid(data)

    def test_provider_metadata_and_request_paths_fail_closed(self):
        data = load_json(ROOT / "catalogs/integrations.json")
        provider = copy.deepcopy(next(item for item in data["integrations"] if item["id"] == "openrouter"))
        data["integrations"] = [provider]
        for key, value in (("mcp_service_enabled", True), ("mcp_service_enabled", 0),
                           ("secret_env", "sk-NEVER-ECHO"), ("secret_env", "KEY\nNEVER-ECHO"),
                           ("endpoint", "https://other.example"), ("endpoint", "//other.example"),
                           ("endpoint", "/chat?token=NEVER-ECHO"), ("endpoint", "/chat#NEVER-ECHO"),
                           ("endpoint", "/../chat"), ("model_selection", "automatic"), ("api_key", "NEVER-ECHO")):
            invalid = copy.deepcopy(data)
            invalid["integrations"][0]["surfaces"][0]["provider"][key] = value
            self._assert_invalid(invalid)
        surface = provider["surfaces"][0]
        surface["config_scope"] = "request"
        surface["native_path_hint"] = "requests/example.json"
        self.assertEqual(self._load_copy(data)["openrouter"].surfaces[0].config_scope, "request")
        for path in ("../escape.json", "C:/secret.json", "requests/CON.json", "requests\\fragment.json"):
            surface["native_path_hint"] = path
            self._assert_invalid(data)

    def test_canonical_ids_and_duplicate_names_are_strict(self):
        data = load_json(ROOT / "catalogs/integrations.json")
        for identifier in ("../bad", "CON", "nul", "UPPER", "contains_space", "space id", "-bad", "bad--id"):
            invalid = copy.deepcopy(data)
            invalid["integrations"][0]["id"] = identifier
            self._assert_invalid(invalid)
            invalid = copy.deepcopy(data)
            invalid["integrations"][0]["surfaces"][0]["id"] = identifier
            self._assert_invalid(invalid)
        data["integrations"][1]["name"] = data["integrations"][0]["name"].upper()
        self._assert_invalid(data)

    def test_strict_schema_rejects_bad_version_and_unknown_keys(self):
        data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
        data["schema_version"] = 2
        self._assert_invalid(data)
        data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
        data["integrations"][0]["unexpected"] = True
        self._assert_invalid(data)
        data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
        data["integrations"][0]["surfaces"][0]["capabilities"]["mcp"]["state"] = "enabled"
        self._assert_invalid(data)
        data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
        data["integrations"][0]["surfaces"][0]["os_workflows"]["plan9"] = "available"
        self._assert_invalid(data)
        data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
        data["integrations"][0]["surfaces"][0]["native_path_hint"] = "../escape.json"
        self._assert_invalid(data)
        for reserved in ("CON", "aux.json", "COM1", "name.", "name "):
            data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
            data["integrations"][0]["surfaces"][0]["native_path_hint"] = reserved
            self._assert_invalid(data)
        data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
        data["integrations"][0]["name"] = "bad\u0000name"
        self._assert_invalid(data)
        data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
        data["integrations"][0]["name"] = "bad\ud800name"
        self._assert_invalid(data)
        data = json.loads((ROOT / "catalogs/integrations.json").read_text(encoding="utf-8"))
        data["integrations"].append(json.loads(json.dumps(data["integrations"][0])))
        self._assert_invalid(data)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalogs").mkdir()
            (root / "catalogs/integrations.json").write_text(
                '{"schema_version": 1, "schema_version": 1, "integrations": []}',
                encoding="utf-8",
            )
            with self.assertRaises(WeaverError) as caught:
                load_integrations(root)
            self.assertNotIn(str(root), str(caught.exception))

    def _assert_invalid(self, data):
        with self.assertRaises(WeaverError) as caught:
            self._load_copy(data)
        self.assertNotIn("NEVER-ECHO", str(caught.exception))

    def _load_copy(self, data):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalogs").mkdir()
            (root / "catalogs/integrations.json").write_text(
                json.dumps(data),
                encoding="utf-8",
            )
            return load_integrations(root)


if __name__ == "__main__":
    unittest.main()
