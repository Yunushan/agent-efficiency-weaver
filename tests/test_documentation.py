import json
import re
import tomllib
import unittest
from pathlib import Path

from agentweaver.adapters import render_mcp
from agentweaver.config import WeaverError
from agentweaver.integrations import load_integrations, load_operating_systems
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
        render_mcp(agent, surface.id, PROFILE, CATALOG, [])
    except WeaverError:
        state = surface.capabilities["mcp"].state
        if state == "unknown":
            return "Unknown"
        if state == "unsupported":
            return "Unsupported"
        return "Not exported"
    if surface.config_scope == "user":
        return "Fragment export; manual import"
    return "Native staged"


class DocumentationDriftTests(unittest.TestCase):
    def setUp(self):
        self.integrations = load_integrations(ROOT)
        self.agents = [item for item in self.integrations.values() if item.kind == "agent"]
        self.agent_surfaces = [(agent, surface) for agent in self.agents for surface in agent.surfaces]

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
        self.assertEqual(len(self.agents), 19)
        self.assertEqual(len(self.agent_surfaces), 21)
        self.assertEqual(
            [item.id for item in self.integrations.values() if item.kind == "provider"],
            ["openrouter"],
        )
        self.assertIn(f"{len(self.agents)} agent integrations", text)
        self.assertIn(f"{len(self.agent_surfaces)} agent surfaces", text)
        self.assertIn("one OpenRouter provider gateway", text)

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

    def test_provider_only_surface_is_represented_with_bundle_gap(self):
        rows = self.product_rows()
        qoder = self.integrations["qoder"]
        self.assertIn("ide", qoder.surface_ids())
        ide = qoder.surface("ide")
        self.assertEqual(ide.capabilities["mcp"].state, "unknown")
        self.assertEqual(ide.capabilities["instructions"].state, "unknown")
        self.assertEqual(ide.capabilities["skills"].state, "unknown")
        self.assertEqual(ide.capabilities["provider"].state, "manual")
        row = rows[("qoder", "ide")]
        self.assertTrue(row[2].startswith("Unknown"))
        self.assertIn("bundle gap", row[3])
        self.assertIn("`manual`", row[5])

    def test_vocabulary_distinguishes_fragments_from_absent_export(self):
        text = read_document("docs/compatibility.md")
        self.assertIn("Fragment export; manual import", text)
        for agent, surface in self.agent_surfaces:
            if surface.config_scope == "user" and expected_mcp(agent, surface).startswith("Fragment"):
                row = self.product_rows()[(agent.id, surface.id)]
                self.assertIn("Fragment export; manual import", row[2])

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


if __name__ == "__main__":
    unittest.main()
