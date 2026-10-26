import json
import re
import tomllib
import unittest
from collections import Counter
from pathlib import Path

from agentweaver.adapters import render_mcp, stdio_inventory
from agentweaver.agents import agent_inventory
from agentweaver.config import WeaverError
from agentweaver.coverage import coverage_report, load_requested_scope
from agentweaver.integrations import load_integrations, load_operating_systems
from agentweaver.models import load_model_hosts, load_model_providers
from agentweaver.providers import render_provider_setup
from agentweaver.skills import render_instruction_bundle

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    "README.md",
    "docs/architecture.md",
    "docs/compatibility.md",
    "docs/roadmap.md",
    "docs/readiness.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "SECURITY.md",
)
OPERATING_SYSTEMS = ("windows", "macos", "linux", "bsd", "solaris", "android", "ios")
PRIMARY_CI = ("windows", "macos", "linux")
FORBIDDEN_CLAIMS = (
    "runtime verified",
    "installed-version verified",
    "verified native support",
    "sso certified",
    "enterprise certified",
    "soc 2",
    "iso 27001",
    "hipaa",
    "native ios app",
    "native android app",
    "autonomous executor",
    "tamper-proof",
    "digitally signed",
    "race-free",
    "whole-bundle atomicity",
    "manual only",
)
PRODUCT_CELL = re.compile(r"`[a-z0-9-]+`")


def read_document(name):
    return (ROOT / name).read_text(encoding="utf-8")


def all_document_text():
    return "\n".join(read_document(name) for name in DOCUMENTS)


def table_rows(text):
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
    return rows


def section(text, heading):
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"Missing documentation section: {heading}")
    return match.group(1)


def clean(value):
    return value.strip().strip("`").strip()


def backticked(value):
    return re.findall(r"`([^`]+)`", value)


PROFILE = json.loads(read_document("profiles/efficient.json"))
CATALOG = json.loads(read_document("catalogs/mcp-servers.json"))


def expected_mcp(agent, surface):
    try:
        artifacts = render_mcp(agent, surface.id, PROFILE, CATALOG, [])
    except WeaverError:
        state = surface.capabilities["mcp"].state
        if state == "unknown":
            return "Unknown"
        if state == "unsupported":
            return "Unsupported"
        return "Not exported"
    if artifacts[0].kind == "mcp-fragment":
        return "Fragment export; manual import"
    if surface.config_scope == "request":
        return "Request fragment"
    return "Native staged"


class DocumentationDriftTests(unittest.TestCase):
    def setUp(self):
        self.integrations = load_integrations(ROOT)
        self.agents = [item for item in self.integrations.values() if item.kind == "agent"]
        self.hybrids = [item for item in self.integrations.values() if item.kind == "hybrid"]
        self.providers = [item for item in self.integrations.values() if item.kind == "provider"]
        self.agent_surfaces = [(agent, surface) for agent in (*self.agents, *self.hybrids) for surface in agent.surfaces]

    def product_rows(self):
        text = section(read_document("docs/compatibility.md"), "Product and surface table")
        rows = [row for row in table_rows(text) if len(row) == 7 and PRODUCT_CELL.fullmatch(row[0])]
        keyed = {}
        for row in rows:
            key = (clean(row[0]), clean(row[1]))
            self.assertNotIn(key, keyed, f"duplicate product row: {key}")
            keyed[key] = row
        return keyed

    def test_counts_and_gateway_are_documented(self):
        text = read_document("docs/compatibility.md")
        self.assertIn(f"{len(self.integrations)} products", text)
        self.assertIn(f"{len(self.agents)} agent integrations", text)
        self.assertIn(f"{len(self.providers)} provider integrations", text)
        self.assertIn(f"{len(self.hybrids)} hybrid integrations", text)
        agent_count = sum(len(item.surfaces) for item in self.agents)
        hybrid_count = sum(len(item.surfaces) for item in self.hybrids)
        self.assertIn(f"{agent_count} agent surfaces", text)
        self.assertIn(f"{hybrid_count} hybrid surfaces", text)
        self.assertIn("25 agents, 4 providers, and 2 hybrids", read_document("README.md"))

    def test_pure_provider_table_covers_every_gateway_surface_and_metadata(self):
        text = section(read_document("docs/compatibility.md"), "Provider surfaces")
        rows = {(clean(row[0]), clean(row[1])): row for row in table_rows(text)
                if len(row) == 6 and PRODUCT_CELL.fullmatch(row[0])}
        expected = {(item.id, surface.id) for item in self.providers for surface in item.surfaces}
        self.assertEqual(set(rows), expected)
        for product in self.providers:
            for surface in product.surfaces:
                row = rows[(product.id, surface.id)]
                self.assertIn(surface.provider["base_url"], row[2])
                self.assertIn(surface.provider["endpoint"], row[3])
                if surface.provider["secret_env"]:
                    self.assertIn(surface.provider["secret_env"], row[4])
                self.assertIn("runtime pending", row[5])

    def test_every_agent_surface_has_exactly_one_row_and_no_extra_rows(self):
        rows = self.product_rows()
        expected = {(agent.id, surface.id) for agent, surface in self.agent_surfaces}
        self.assertEqual(set(rows), expected)

    def test_mcp_column_matches_adapter_outcome(self):
        rows = self.product_rows()
        for agent, surface in self.agent_surfaces:
            with self.subTest(agent=agent.id, surface=surface.id):
                row = rows[(agent.id, surface.id)]
                self.assertTrue(row[2].startswith(expected_mcp(agent, surface)), row[2])
                if surface.native_path_hint:
                    self.assertIn(surface.native_path_hint, row[4])
                self.assertNotIn("no export", row[2].lower())
                self.assertNotIn("manual only", row[2].lower())

    def test_instruction_and_skill_column_matches_registry_or_declares_gap(self):
        rows = self.product_rows()
        for agent, surface in self.agent_surfaces:
            with self.subTest(agent=agent.id, surface=surface.id):
                row = rows[(agent.id, surface.id)]
                instructions = surface.capabilities["instructions"].state
                skills = surface.capabilities["skills"].state
                if instructions in {"unknown", "unsupported"} and skills in {"unknown", "unsupported"}:
                    self.assertIn("bundle gap", row[3])
                else:
                    self.assertIn(surface.instruction_mode, row[3])
                    self.assertIn(f"skills `{skills}`", row[3])

    def test_scope_and_provider_columns_match_registry(self):
        rows = self.product_rows()
        for agent, surface in self.agent_surfaces:
            with self.subTest(agent=agent.id, surface=surface.id):
                row = rows[(agent.id, surface.id)]
                self.assertIn(surface.config_scope, row[4])
                provider_state = surface.capabilities["provider"].state
                self.assertIn(f"`{provider_state}`", row[5])

    def test_evidence_column_reports_dates_and_runtime_status(self):
        rows = self.product_rows()
        for agent, surface in self.agent_surfaces:
            with self.subTest(agent=agent.id, surface=surface.id):
                row = rows[(agent.id, surface.id)]
                capabilities = surface.capabilities.values()
                for capability in capabilities:
                    if capability.checked_on:
                        self.assertIn(capability.checked_on, row[6])
                if any(capability.runtime_checks for capability in capabilities):
                    self.assertIn("runtime recorded", row[6])
                else:
                    self.assertIn("runtime pending", row[6])

    def test_qoder_ide_distinguishes_app_mcp_fragment_from_native_instructions_and_skills(self):
        rows = self.product_rows()
        qoder = self.integrations["qoder"]
        self.assertIn("ide", qoder.surface_ids())
        ide = qoder.surface("ide")
        self.assertEqual(ide.config_scope, "app")
        self.assertIsNone(ide.native_path_hint)
        self.assertEqual(ide.capabilities["mcp"].state, "manual")
        self.assertEqual(ide.capabilities["instructions"].state, "available")
        self.assertEqual(ide.capabilities["skills"].state, "available")
        self.assertEqual(ide.skills_path, ".qoder/skills")
        self.assertEqual(ide.capabilities["provider"].state, "manual")
        row = rows[("qoder", "ide")]
        self.assertTrue(row[2].startswith("Fragment export; manual import"))
        self.assertNotIn("bundle gap", row[3])
        self.assertIn("`manual`", row[5])
        mcp = render_mcp(qoder, "ide", PROFILE, CATALOG, [])
        self.assertEqual(mcp[0].kind, "mcp-fragment")
        self.assertEqual(mcp[0].relative_path.as_posix(), "qoder/fragments/mcp.ide.json")
        bundle = render_instruction_bundle(qoder, "ide", PROFILE, ["token-efficient-retrieval"], ROOT)
        self.assertIn("qoder/AGENTS.md", [item.relative_path.as_posix() for item in bundle])
        self.assertIn("qoder/.qoder/skills/token-efficient-retrieval/SKILL.md",
                      [item.relative_path.as_posix() for item in bundle])

    def test_vocabulary_distinguishes_fragments_from_absent_export(self):
        text = read_document("docs/compatibility.md")
        self.assertIn("Fragment export; manual import", text)
        for agent, surface in self.agent_surfaces:
            if surface.config_scope == "user" and expected_mcp(agent, surface).startswith("Fragment"):
                row = self.product_rows()[(agent.id, surface.id)]
                self.assertIn("Fragment export; manual import", row[2])

    def test_stdio_table_matches_documented_transport_routes_and_evidence(self):
        text = section(read_document("docs/compatibility.md"), "MCP transports and stdio staging")
        rows = {clean(row[0]): row for row in table_rows(text)
                if len(row) == 4 and PRODUCT_CELL.fullmatch(row[0])}
        routes = stdio_inventory()
        self.assertEqual(set(rows), set(routes))
        for product, route in routes.items():
            row = rows[product]
            documented = [route, *route.get("additional_routes", [])]
            self.assertEqual(set(backticked(row[1])), {item["mcp_dialect"] for item in documented})
            self.assertEqual(set(backticked(row[2])), {item["schema"] for item in documented})
            self.assertEqual(set(re.findall(r"\]\((https://[^)]+)\)", row[3])),
                             {url for item in documented for url in item["sources"]})
            self.assertEqual(set(backticked(row[3])),
                             {reference for item in documented for reference in item["fixture_tests"]})
            for item in documented:
                self.assertIn(item["checked_on"], row[3])
                self.assertEqual(item["runtime_checks"], [])
            self.assertIn("runtime pending", row[3])
        surface_count = sum(surface.mcp_dialect in {
                                item["mcp_dialect"] for item in
                                [routes[product.id], *routes[product.id].get("additional_routes", [])]}
                            for product in self.integrations.values() if product.id in routes
                            for surface in product.surfaces)
        self.assertIn(f"{len(routes)} products", text)
        self.assertIn(f"{surface_count} matching registry surfaces", text)

    def test_stdio_guide_keeps_argv_staged_default_off_and_request_routes_remote(self):
        text = section(read_document("docs/compatibility.md"), "MCP transports and stdio staging")
        for token in ("default_enabled: false", "profile permission", "--enable", "structured `args` array",
                      "1,024", "128 arguments", "4,096", "32,768", "environment maps", "variable expansion",
                      "never executes", "invokes a shell", "installs a package", "credential-looking",
                      "Perplexity Agent API", "LM Studio ephemeral", "remote HTTPS only", "reject selected stdio",
                      "JetBrains Air", "OpenHands CLI", "stdio file-export gap", "not installed-client evidence",
                      "manual user MCP fragment", "CLI 1.16.0 source"):
            self.assertIn(token, text)
        self.assertNotIn("OpenHands retain stdio file-export gaps", text)

    def test_operating_system_table_covers_seven_families(self):
        systems = load_operating_systems(ROOT)
        self.assertEqual(set(systems), set(OPERATING_SYSTEMS))
        text = section(read_document("docs/compatibility.md"), "Operating-system workflows")
        rows = {
            clean(row[0]): row
            for row in table_rows(text)
            if len(row) == 3 and PRODUCT_CELL.fullmatch(row[0])
        }
        self.assertEqual(set(rows), set(OPERATING_SYSTEMS))
        for name in OPERATING_SYSTEMS:
            with self.subTest(os=name):
                lowered = " ".join(rows[name]).lower()
                if name in PRIMARY_CI:
                    self.assertIn("primary ci target", lowered)
                else:
                    self.assertNotIn("primary ci target", lowered)
        for name in ("bsd", "solaris"):
            lowered = " ".join(rows[name]).lower()
            self.assertIn("conditional", lowered)
            self.assertIn("pending", lowered)
            for variant in systems[name].variants:
                self.assertIn(variant, lowered)
        self.assertIn("termux", " ".join(rows["android"]).lower())
        self.assertIn("remote", " ".join(rows["android"]).lower())
        self.assertIn("remote", " ".join(rows["ios"]).lower())
        self.assertIn("artifact", " ".join(rows["ios"]).lower())

    def test_provider_handoff_table_matches_adapter_outcomes(self):
        text = section(read_document("docs/compatibility.md"), "OpenRouter provider handoffs")
        documented = {}
        gaps = set()
        for row in table_rows(text):
            if len(row) != 4 or not PRODUCT_CELL.fullmatch(row[0]):
                continue
            key = (clean(row[0]), clean(row[1]))
            if clean(row[2]).startswith("Documented gap"):
                gaps.add(key)
            else:
                self.assertTrue(clean(row[2]).startswith("Handoff"), row[2])
                documented[key] = backticked(row[3])
        actual = {}
        failing = set()
        for agent, surface in self.agent_surfaces:
            key = (agent.id, surface.id)
            try:
                artifacts = render_provider_setup(
                    agent, surface.id, provider_id="openrouter", model_id="vendor/model"
                )
            except WeaverError:
                failing.add(key)
            else:
                actual[key] = [item.relative_path.as_posix() for item in artifacts]
        self.assertEqual(set(actual), set(documented))
        for key, paths in actual.items():
            with self.subTest(provider=key):
                self.assertEqual(documented[key], paths)
        self.assertEqual(failing, gaps)
        self.assertIn(("kiro", "ide"), gaps)
        self.assertIn(("antigravity", "ide"), gaps)
        self.assertIn(("devin", "desktop-local"), gaps)
        self.assertIn(("qoder", "cli"), gaps)
        self.assertIn(("codex", "cli"), actual)
        self.assertIn("codex/provider/config.toml", documented[("codex", "cli")])

    def test_provider_text_names_variables_and_never_reads_credentials(self):
        lowered = all_document_text().lower()
        for token in ("provider-setup.md", "config.toml", "env_key", "names variables only",
                      "never reads credential environment-variable values"):
            self.assertIn(token, lowered)
        self.assertNotIn("reads the key from", lowered)

    def test_abacus_uses_root_agents_md_and_no_native_skill_directory(self):
        names = sorted(path.name for path in (ROOT / ".agents/skills").iterdir() if path.is_dir())
        artifacts = render_instruction_bundle(
            self.integrations["abacus"], "desktop", PROFILE, names, ROOT
        )
        paths = [item.relative_path.as_posix() for item in artifacts]
        self.assertIn("abacus/AGENTS.md", paths)
        for path in paths:
            self.assertNotIn("skills", path.split("/"))
            self.assertNotIn(".abacusai", path.split("/"))
        self.assertIn("root `AGENTS.md`", read_document("docs/compatibility.md"))
        self.assertIn("no native skill directory", read_document("docs/compatibility.md").lower())

    def test_manual_read_files_and_handoff_paths_are_documented(self):
        lowered = all_document_text().lower()
        for token in ("`<agent>/read/<name>.md`", "handoff/instructions.md", "fragments/",
                      "read files", "portable read files"):
            self.assertIn(token, lowered)

    def test_mypy_pipeline_is_strict(self):
        project = tomllib.loads(read_document("pyproject.toml"))
        mypy = project["tool"]["mypy"]
        self.assertIs(mypy["disallow_untyped_defs"], True)
        self.assertIs(mypy["check_untyped_defs"], True)
        self.assertIs(mypy["warn_unused_ignores"], True)
        self.assertIs(mypy["no_implicit_optional"], True)
        contributing = read_document("CONTRIBUTING.md")
        self.assertIn("python -m mypy src/agentweaver", contributing)
        self.assertIn("disallow_untyped_defs", contributing)

    def test_development_tools_and_gates_match_pins(self):
        project = tomllib.loads(read_document("pyproject.toml"))
        dev = project["project"]["optional-dependencies"]["dev"]
        contributing = read_document("CONTRIBUTING.md")
        for requirement in dev:
            self.assertIn(requirement, contributing)
        for command in (
            "python -m agentweaver validate",
            "python -m unittest discover -s tests -v",
            "python -m ruff check .",
            "python -m mypy src/agentweaver",
            "python -m build --no-isolation",
            "python scripts/check-distribution.py",
        ):
            self.assertIn(command, contributing)
        self.assertIn('python -m pip install ".[dev]"', contributing)
        self.assertIn("offline", contributing.lower())
        self.assertIn("scripts/verify-platform.py", contributing)
        self.assertIn("--output", contributing)
        self.assertIn("separate manual check", contributing.lower())

    def test_evidence_and_inventory_states_are_explained(self):
        text = read_document("docs/compatibility.md")
        lowered = text.lower()
        for token in ("sources", "checked_on", "fixture_tests", "runtime_checks", "available", "manual",
                      "unsupported", "unknown", "inventory"):
            self.assertIn(token, lowered)
        self.assertIn("fixture tests and runtime checks are independent", lowered)
        self.assertIn("no installed-version or native-device verification is recorded", lowered)

    def test_documents_do_not_claim_unverified_runtime_or_entitlements(self):
        text = all_document_text()
        lowered = text.lower()
        for phrase in FORBIDDEN_CLAIMS:
            self.assertNotIn(phrase, lowered)
        self.assertIsNone(re.search(r"\b\d+(?:\.\d+)?\s?%", text))
        for line in text.splitlines():
            lowered_line = line.lower()
            if "sso" in lowered_line or "entitlement" in lowered_line or "compliance certification" in lowered_line:
                self.assertTrue(
                    any(negative in lowered_line for negative in ("not ", "no ", "out of scope")),
                    line,
                )

    def test_security_and_default_off_contract_is_explicit(self):
        lowered = all_document_text().lower()
        for token in ("default-off", "enterprise-secure", "zero", "ambient",
                      "not inspected", "advisory", "not tool enforcement"):
            self.assertIn(token, lowered)

    def test_staging_ownership_and_limit_claims_are_explicit(self):
        lowered = read_document("docs/compatibility.md").lower()
        for token in ("--force", "owned", "sha-256", "manifest", "junctions", "secrets", "absolute",
                      "not a signature", "not a bundle-wide atomic transaction",
                      "not a cross-process race guarantee"):
            self.assertIn(token, lowered)

    def test_cli_and_migration_contract_is_documented(self):
        lowered = all_document_text().lower()
        for token in ("inventory", "render", "bundle", "provider", "doctor", "report", "compare",
                      "--platform kilo|abacus", "--agent", "mutually exclusive", "enabled: true",
                      "schema_version: 1", "artifacts", "path", "kind", "content", "importlib.resources",
                      "--root .", "pythonpath=src", "no checkout or cwd fallback", "--skill", "--model",
                      "--dry-run", "api-key values are never accepted as arguments"):
            self.assertIn(token, lowered)

    def test_requested_scope_is_accounting_and_preserves_product_boundaries(self):
        text = section(read_document("docs/compatibility.md"), "Requested scope and aliases")
        scope = load_requested_scope(ROOT)
        targets = {(target.product_id, target.surface_id) for item in scope for target in item.targets}
        self.assertIn(f"{len(scope)} requested groups", text)
        self.assertIn(f"{len(targets)} distinct registry surfaces", text)
        summary = coverage_report(ROOT)["summary"]
        self.assertIn(f"{summary['missing_documentation_capabilities']} missing documentation records", text)
        self.assertIn(f"All {summary['pending_runtime_capabilities']} requested capability runtime records remain pending", text)
        for state, count in summary["capability_states"].items():
            self.assertIn(f"{count} {state}", text)
        for token in ("unknown", "missing documentation", "runtime", "not a native support result",
                      "VSCodeagents", "concrete product", "JetBrainsAir", "never `junie`"):
            self.assertIn(token, text)

    def test_requested_os_table_matches_all_surface_states_constraints_and_sources(self):
        text = section(read_document("docs/compatibility.md"), "Requested surface OS workflows")
        rows = {(clean(row[0]), clean(row[1])): row for row in table_rows(text)
                if len(row) == 10 and PRODUCT_CELL.fullmatch(row[0])}
        targets = {(target.product_id, target.surface_id)
                   for item in load_requested_scope(ROOT) for target in item.targets}
        self.assertEqual(set(rows), targets)
        symbols = {"available": "A", "manual": "M", "unknown": "?", "unsupported": "X"}
        for product, surface_id in targets:
            row = rows[(product, surface_id)]
            surface = self.integrations[product].surface(surface_id)
            self.assertEqual([clean(cell) for cell in row[2:9]],
                             [symbols[surface.os_workflows[os_id]] for os_id in OPERATING_SYSTEMS])
            evidence = surface.capabilities["os"]
            if evidence.version_constraint:
                self.assertIn(evidence.version_constraint, row[9])
            for source in evidence.sources:
                self.assertIn(source, row[9])
        for token in ("documented installation/platform route", "All vendor runtime checks remain pending",
                      "BSD and Solaris remain unknown", "FreeBSD", "every BSD variant", "aarch64 Termux",
                      "not a general mobile app claim", "Bionic is a separate app", "approved reachable endpoint",
                      "without", "Windows 10 1809+", "x64/ARM64"):
            self.assertIn(token, text)

    def test_manual_vendor_skill_imports_keep_standard_filename_and_declared_limits(self):
        text = section(read_document("docs/compatibility.md"), "Instruction and skill bundles")
        for product, surface in (("perplexity", "app"), ("lm-studio", "bionic")):
            artifacts = render_instruction_bundle(self.integrations[product], surface, PROFILE,
                                                ["token-efficient-retrieval"], ROOT)
            imports = [item for item in artifacts if item.kind == "skill-import"]
            self.assertEqual(len(imports), 1)
            self.assertEqual(imports[0].relative_path.as_posix(),
                             f"{product}/imports/skills/token-efficient-retrieval/SKILL.md")
        for token in ("imports/skills/<name>/SKILL.md", "skill-import", "not a native discovery path",
                      "No upload or import is performed"):
            self.assertIn(token, text)

    def test_model_provider_route_table_matches_declared_protocols(self):
        text = section(read_document("docs/compatibility.md"), "Models and reasoning")
        rows = {clean(row[0]): row for row in table_rows(text)
                if len(row) == 3 and PRODUCT_CELL.fullmatch(row[0])}
        providers = load_model_providers(ROOT)
        self.assertEqual(set(rows), set(providers))
        for identifier, provider in providers.items():
            row = rows[identifier]
            self.assertEqual(backticked(row[1]), [provider["integration_id"], provider["surface"]])
            self.assertEqual(backticked(row[2]), provider["protocols"])
        lowered = text.lower()
        for token in ("not an exhaustive", "explicit model", "snapshot", "every supplied page",
                      "catalog completeness stays unknown", "not establish current access",
                      "reasoning-effort", "reasoning-budget", "reasoning-mode", "--context-size",
                      "different wire formats", "fails", "never read credential", "paid calls"):
            self.assertIn(token, lowered)

    def test_custom_agent_guide_separates_definitions_skills_and_permissions(self):
        text = section(read_document("docs/compatibility.md"), "Custom agent definitions")
        for token in ("agents inventory", "instructions-file", "agent-definition", "agent-handoff",
                      "HANDOFF.md", "portable Skills", "unknown", "unsupported", "fail closed",
                      "do not grant permissions", "reasoning", "manual"):
            self.assertIn(token, text)

    def test_host_model_controls_table_covers_every_surface_and_declared_control(self):
        text = section(read_document("docs/compatibility.md"), "Host model controls")
        rows = {(clean(row[0]), clean(row[1])): row for row in table_rows(text)
                if len(row) == 5 and PRODUCT_CELL.fullmatch(row[0])}
        hosts = load_model_hosts(ROOT)
        self.assertEqual(set(rows), {(row["product_id"], row["surface_id"]) for row in hosts})
        self.assertIn(f"{len(hosts)} registered surfaces", text)
        for host in hosts:
            row = rows[(host["product_id"], host["surface_id"])]
            selection, reasoning = host["selection"], host["reasoning"]
            self.assertIn(f"`{selection['state']}`", row[2])
            self.assertIn(selection["mechanism"], row[2])
            for field in (*selection["fields"], *selection["commands"]):
                self.assertIn(f"`{field}`", row[2])
            for step in selection["ui_steps"]:
                self.assertIn(step, row[2])
            self.assertIn(f"`{reasoning['state']}`", row[3])
            for control in reasoning["controls"]:
                self.assertIn(f"`{control['kind']}`", row[3])
                self.assertIn(f"`{control['field']}`", row[3])
                if control["values"] is None:
                    self.assertIn("model-specific discovery", row[3])
                else:
                    for value in control["values"]:
                        self.assertIn(f"`{value}`", row[3])
            self.assertIn(f"`{host['availability']}`", row[4])
            self.assertIn(host["checked_on"], row[4])
            self.assertIn("runtime pending", row[4])
        for token in ("do not enable a native exporter", "Null control values", "model-dependent",
                      "unknown shared reasoning override", "external agents", "project-local provider keys are ignored"):
            self.assertIn(token, text)

    def test_custom_agent_table_covers_all_registry_surfaces_and_declared_fields(self):
        text = section(read_document("docs/compatibility.md"), "Custom agent definitions")
        rows = {(clean(row[0]), clean(row[1])): row for row in table_rows(text)
                if len(row) == 5 and PRODUCT_CELL.fullmatch(row[0])}
        definitions = agent_inventory(ROOT)["definitions"]
        self.assertEqual(set(rows), {(row["product_id"], row["surface_id"]) for row in definitions})
        counts = Counter(row["state"] for row in definitions)
        for state, count in counts.items():
            phrase = "native definition routes" if state == "native" else f"{state} routes"
            self.assertIn(f"{count} {phrase}", text)
        for definition in definitions:
            row = rows[(definition["product_id"], definition["surface_id"])]
            self.assertEqual(clean(row[2]), definition["state"])
            self.assertEqual(clean(row[3]), definition["path_template"] or "none")
            for field in ("model_field", "effort_field"):
                if definition[field]:
                    self.assertIn(f"`{definition[field]}`", row[4])
        for token in ("inherit", "ignore model selection", "explicit model", "provider",
                      "No maximum effort", "Permission defaults", "runtime checks remain pending"):
            self.assertIn(token, text)
        for token in ("not the parent model", "router", "admin-pinned", "rejects an explicit `inherit`",
                      "Subagents (Preview)", "experimental", "true tool restriction", "auto-approval semantics",
                      "authorized project admin", "versioned Profile", "not a complete Profile JSON schema",
                      "not an authorization boundary", "does not revoke ambient Profile access",
                      "five requested custom-definition routes", "no distinct registration contract"):
            self.assertIn(token, text)

    def test_openhands_guide_binds_cli_loaders_without_promoting_profiles_or_runtime(self):
        compatibility = read_document("docs/compatibility.md")
        definitions = section(compatibility, "Custom agent definitions")
        for token in ("CLI 1.16.0/SDK 1.28.1", "model: inherit", "tools: []",
                      "saved local LLM profiles", "not raw provider model IDs", "top-level Markdown",
                      "base finish/think", "No MCP, hooks, permission modes or OS sandbox",
                      "not installed-client verification", "--llm-profile NAME", "by default",
                      "existing named local LLM profile", "preserves the exact supplied reference",
                      "without reading the profile store or credentials", "checking existence",
                      "creating a profile", "1–64 ASCII", "optional lowercase `.json`",
                      "keeps its suffix and case", "literal case-sensitive `inherit`",
                      "`inherit.json` and `Inherit`", "other products reject `--llm-profile`",
                      "conflicts with `--model`, `--provider`, `--catalog`, and `--reasoning-effort`"):
            self.assertIn(token, definitions)
        route = next(row for row in agent_inventory(ROOT)["definitions"]
                     if (row["product_id"], row["surface_id"]) == ("openhands", "cli"))
        self.assertEqual((route["model_style"], route["model_field"]), ("local-profile", "model"))
        profile_source = next(source for source in route["evidence"]["sources"]
                              if "llm_profile_store.py" in source)
        self.assertIn(profile_source, definitions)
        skills = section(compatibility, "Instruction and skill bundles")
        for token in ("working directory and then Git root", "immediate child directories",
                      "no general ancestor or category recursion", "not installed-client discovery"):
            self.assertIn(token, skills)
        evidence = self.integrations["openhands"].surface("cli").capabilities["skills"]
        self.assertEqual(set(re.findall(r"\]\((https://[^)]+)\)", skills)), set(evidence.sources))
        controls = section(compatibility, "Host model controls")
        for token in ("JetBrains Air desktop alone", "unknown shared reasoning override", "Agent.llm",
                      "llm.reasoning_effort", "llm.extended_thinking_budget", "model-feature dependent",
                      "Responses subscription route", "capped below `max_output_tokens`"):
            self.assertIn(token, controls)
        provider = section(compatibility, "OpenRouter provider handoffs")
        for token in ("press `Ctrl+P`", "choose Settings", "--override-with-envs", "not persisted",
                      "llm.model", "llm.base_url", "exports no complete settings file"):
            self.assertIn(token, provider)
        self.assertNotIn("README marks this CLI unmaintained", controls)

    def test_zero_context_inventory_is_distinct_from_usable_configuration(self):
        text = section(read_document("docs/compatibility.md"), "Models and reasoning")
        for token in ("context_length: 0", "remains in normalized inventory", "context_size",
                      "`None` (unknown)", "not a configured zero-length window", "--context-size 0",
                      "invalid", "positive", "Kimi native configuration", "manual template"):
            self.assertIn(token, text)
        self.assertIn("derived usable `context_size` remains `None`", read_document("docs/architecture.md"))

    def test_recorded_ci_checkpoint_is_explicitly_historical_for_expansion(self):
        text = section(read_document("docs/readiness.md"), "Recorded core CLI CI evidence")
        for token in ("historical evidence", "earlier 20-product", "does not verify", "31-product expansion",
                      "cf5bcdc2b1bacc03df355f24e3f6e6ab55998dad", "40278860777"):
            self.assertIn(token, text)

    def test_expanded_ci_and_runtime_record_contract_remain_commit_and_host_scoped(self):
        readiness = read_document("docs/readiness.md")
        text = section(readiness, "Recorded expanded CLI CI evidence")
        for token in ("40295841872", "b2ef30fe02974a24bf8d0f4c804e908fd9ba338d", "PR #2",
                      "subsequent changes require their own", "pending vendor runtime"):
            self.assertIn(token, text)
        for token in ("product_version", "os_variant", "architecture", "checked_on", "artifact_sha256",
                      "configuration-consumption", "operator-supplied attestations", "Conflicting aliases",
                      "regardless of input order", "no latest-date rule"):
            self.assertIn(token, readiness)


if __name__ == "__main__":
    unittest.main()
