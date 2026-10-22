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
    ("amp", "cli"),
    ("claude-code", "cli"),
}

INSTRUCTION_SURFACES = ADAPTER_SURFACES | {("aider", "cli")}
SKILL_SURFACES = INSTRUCTION_SURFACES - {("cline", "ide"), ("cursor", "ide")}

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
    ("abacus", "desktop"): "manual",
    ("qoder", "ide"): "manual",
    ("amp", "cli"): "manual",
    ("claude-code", "cli"): "manual",
    ("aider", "cli"): "manual",
}

PROVIDER_GAPS = {
    ("kiro", "ide"),
    ("antigravity", "ide"),
    ("devin", "desktop-local"),
    ("qoder", "cli"),
}



class IntegrationRegistryTests(unittest.TestCase):
    def setUp(self):
        self.integrations = load_integrations(ROOT)

    def test_exact_inventory_and_identity(self):
        self.assertEqual(set(self.integrations), EXPECTED_IDS)
        self.assertEqual(len(self.integrations), 20)
        self.assertEqual(len({item.name for item in self.integrations.values()}), 20)
        self.assertEqual(
            {item.id for item in self.integrations.values()},
            EXPECTED_IDS,
        )
        self.assertEqual(
            [item.id for item in self.integrations.values() if item.kind == "agent"],
            sorted(item.id for item in self.integrations.values() if item.kind == "agent"),
        )

    def test_kinds_and_defaults(self):
        self.assertEqual(
            {item.id for item in self.integrations.values() if item.kind == "agent"},
            EXPECTED_IDS - {"openrouter"},
        )
        self.assertEqual(
            {item.id for item in self.integrations.values() if item.kind == "provider"},
            {"openrouter"},
        )
        self.assertEqual(
            {item.id: item.default_surface for item in self.integrations.values()},
            EXPECTED_DEFAULT_SURFACES,
        )
        for item in self.integrations.values():
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
                self.assertIn(surface.config_scope, {"project", "user", "app", "none"})
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
                if path is None or surface.config_scope != "project":
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
        self.assertEqual(
            self.integrations["kilo"].surface("cli").capabilities["os"].state,
            "unknown",
        )
        self.assertEqual(
            self.integrations["aider"].surface("cli").capabilities["os"].state,
            "unknown",
        )
        pi_os = self.integrations["pi"].surface("cli").capabilities["os"]
        self.assertEqual(pi_os.state, "manual")
        self.assertTrue(all("pi.dev" in source for source in pi_os.sources))
        cursor_os = self.integrations["cursor"].surface("ide").capabilities["os"]
        self.assertEqual(cursor_os.state, "manual")
        self.assertTrue(all("cursor.com" in source for source in cursor_os.sources))

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

    def test_provider_metadata_is_on_openrouter_only(self):
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
            if integration.id == "openrouter":
                continue
            for surface in integration.surfaces:
                self.assertIsNone(surface.provider)

    def test_documentation_and_runtime_evidence_are_independent(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                for evidence in surface.capabilities.values():
                    self.assertIsInstance(evidence.sources, tuple)
                    self.assertIsInstance(evidence.fixture_tests, tuple)
                    self.assertIsInstance(evidence.runtime_checks, tuple)
                    self.assertEqual(evidence.runtime_checks, ())
                    if evidence.state != "unknown":
                        self.assertEqual(evidence.checked_on, "2026-10-22")

    def test_mcp_fixtures_cover_only_exported_surfaces(self):
        for integration in self.integrations.values():
            for surface in integration.surfaces:
                expected = (
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
                expected_instructions = (
                    ("tests/test_skills.py",) if key in INSTRUCTION_SURFACES else ()
                )
                expected_skills = (
                    ("tests/test_skills.py",) if key in SKILL_SURFACES else ()
                )
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
            if integration.kind == "provider":
                continue
            for surface in integration.surfaces:
                key = (integration.id, surface.id)
                evidence = surface.capabilities["provider"]
                if key in PROVIDER_ROUTES:
                    documented = provider_evidence(integration.id, surface.id)
                    self.assertEqual(evidence.state, PROVIDER_ROUTES[key], key)
                    self.assertEqual(evidence.sources, tuple(documented["sources"]), key)
                    self.assertEqual(evidence.checked_on, documented["checked_on"], key)
                    self.assertEqual(
                        evidence.fixture_tests,
                        ("tests/test_providers.py",),
                        key,
                    )
                else:
                    self.assertIn(key, PROVIDER_GAPS, key)
                    self.assertEqual(evidence.state, "unknown", key)
                    self.assertEqual(evidence.sources, (), key)
                    self.assertIsNone(evidence.checked_on, key)
                    self.assertEqual(evidence.fixture_tests, (), key)
                self.assertIsNone(evidence.version_constraint, key)
                self.assertEqual(evidence.runtime_checks, (), key)

    def test_qoder_ide_is_a_provider_only_handoff_surface(self):
        qoder = self.integrations["qoder"]
        self.assertEqual(qoder.default_surface, "cli")
        self.assertEqual(qoder.surface_ids(), ("cli", "ide"))
        cli = qoder.surface("cli")
        self.assertEqual(cli.native_path_hint, ".qoder/settings.json")
        self.assertEqual(cli.mcp_dialect, "mcpServers.type-http")
        self.assertEqual(cli.capabilities["provider"].state, "unknown")
        self.assertEqual(cli.capabilities["provider"].sources, ())

        ide = qoder.surface("ide")
        self.assertIsNone(ide.mcp_dialect)
        self.assertEqual(ide.config_fields, ())
        self.assertEqual(ide.config_scope, "none")
        self.assertIsNone(ide.native_path_hint)
        self.assertIsNone(ide.documented_location)
        self.assertEqual(ide.instruction_mode, "manual")
        self.assertEqual(ide.instruction_paths, ())
        self.assertIsNone(ide.skills_path)
        self.assertEqual(ide.os_workflows, {os_id: "unknown" for os_id in OS_IDS})
        for name in ("mcp", "instructions", "skills", "os"):
            evidence = ide.capabilities[name]
            self.assertEqual(evidence.state, "unknown", name)
            self.assertEqual(evidence.sources, (), name)
            self.assertIsNone(evidence.checked_on, name)
            self.assertEqual(evidence.fixture_tests, (), name)
            self.assertEqual(evidence.runtime_checks, (), name)
            self.assertIsNone(evidence.version_constraint, name)
        provider = ide.capabilities["provider"]
        documented = provider_evidence("qoder", "ide")
        self.assertEqual(provider.state, "manual")
        self.assertEqual(provider.sources, tuple(documented["sources"]))
        self.assertEqual(provider.checked_on, documented["checked_on"])
        self.assertEqual(provider.fixture_tests, ("tests/test_providers.py",))
        self.assertEqual(provider.runtime_checks, ())
        self.assertIsNone(provider.version_constraint)

    def test_qoder_ide_fails_non_provider_exports(self):
        qoder = self.integrations["qoder"]
        profile = load_json(ROOT / "profiles/efficient.json")
        catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        with self.assertRaises(WeaverError) as mcp_error:
            render_mcp(qoder, "ide", profile, catalog, [])
        self.assertIn("unavailable", str(mcp_error.exception).casefold())
        with self.assertRaises(WeaverError) as bundle_error:
            render_instruction_bundle(qoder, "ide", profile, [], ROOT)
        self.assertIn("unknown", str(bundle_error.exception).casefold())


    def test_get_integration_and_surface(self):
        self.assertEqual(get_integration("codex", ROOT).id, "codex")
        self.assertEqual(get_surface("codex", "cli", ROOT).id, "cli")
        with self.assertRaises(WeaverError):
            get_integration("missing", ROOT)
        with self.assertRaises(WeaverError):
            get_integration("", ROOT)
        with self.assertRaises(WeaverError):
            get_surface("codex", "desktop", ROOT)

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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalogs").mkdir()
            (root / "catalogs/integrations.json").write_text(
                json.dumps(data),
                encoding="utf-8",
            )
            with self.assertRaises(WeaverError):
                load_integrations(root)


if __name__ == "__main__":
    unittest.main()
