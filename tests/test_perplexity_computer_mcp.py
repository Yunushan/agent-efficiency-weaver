"""Offline staging contracts for the distinct, opt-in Computer MCP endpoint."""
import json
import tomllib
import unittest
from pathlib import Path

from agentweaver.adapters import render_mcp
from agentweaver.config import WeaverError, load_json, select_servers, validate_catalog
from agentweaver.integrations import load_integrations

ROOT = Path(__file__).resolve().parents[1]
SERVER = "perplexity-computer"
URL = "https://www.perplexity.ai/rest/computer/mcp"
SOURCE = "https://docs.perplexity.ai/docs/getting-started/integrations/computer-mcp-server"


class PerplexityComputerMcpTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        self.profile = load_json(ROOT / "profiles/efficient.json")
        self.integrations = load_integrations(ROOT)

    def document(self, product, profile, enabled):
        spec = self.integrations[product]
        artifact = render_mcp(spec, spec.default_surface, profile, self.catalog, enabled)[0]
        if product == "codex":
            return tomllib.loads(artifact.content.decode("utf-8"))
        return json.loads(artifact.content)

    def test_catalog_records_distinct_default_off_account_authenticated_service(self):
        self.assertEqual(validate_catalog(self.catalog), [])
        entry = self.catalog["servers"][SERVER]
        self.assertEqual(set(entry), {"description", "kind", "url", "default_enabled"})
        self.assertEqual(entry["kind"], "remote")
        self.assertEqual(entry["url"], URL)
        self.assertIs(entry["default_enabled"], False)
        for fact in ("OAuth 2.0", "account credits", "account-scoped models_list", SOURCE):
            self.assertIn(fact, entry["description"])
        self.assertEqual(self.catalog["servers"]["perplexity"]["url"],
                         "https://api.perplexity.ai/mcp")
        self.assertNotEqual(entry["url"], self.catalog["servers"]["perplexity"]["url"])

    def test_shipped_profiles_do_not_implicitly_allow_computer_service(self):
        for path in sorted((ROOT / "profiles").glob("*.json")):
            with self.subTest(profile=path.name):
                profile = load_json(path)
                self.assertNotIn(SERVER, profile["allowed_mcp"])
                self.assertEqual(select_servers(profile, self.catalog, []), {})
                with self.assertRaises(WeaverError):
                    select_servers(profile, self.catalog, [SERVER])

    def test_allowlisting_without_explicit_selection_keeps_documents_empty(self):
        profile = dict(self.profile, allowed_mcp=[SERVER])
        for product, key in (("codex", "mcp_servers"), ("copilot", "servers"),
                             ("abacus", "mcpServers")):
            with self.subTest(product=product):
                self.assertEqual(self.document(product, profile, []), {key: {}})

    def test_explicit_selection_without_allowlisting_fails_for_each_dialect(self):
        for product in ("codex", "copilot", "abacus"):
            with self.subTest(product=product), self.assertRaises(WeaverError):
                self.document(product, self.profile, [SERVER])

    def test_explicit_selection_renders_only_documented_transport_fields(self):
        profile = dict(self.profile, allowed_mcp=[SERVER])
        cases = (
            ("codex", "mcp_servers", {"url": URL}),
            ("copilot", "servers", {"type": "http", "url": URL}),
            ("abacus", "mcpServers", {"url": URL}),
        )
        for product, key, entry in cases:
            with self.subTest(product=product):
                self.assertEqual(self.document(product, profile, [SERVER]),
                                 {key: {SERVER: entry}})

    def test_api_and_computer_endpoints_can_be_selected_without_conflation(self):
        profile = dict(self.profile, allowed_mcp=["perplexity", SERVER], max_mcp_servers=2)
        self.assertEqual(select_servers(profile, self.catalog, [SERVER, "perplexity"]), {
            "perplexity": "https://api.perplexity.ai/mcp",
            SERVER: URL,
        })
        self.assertEqual(self.document("codex", profile, [SERVER, "perplexity"]), {
            "mcp_servers": {
                "perplexity": {"url": "https://api.perplexity.ai/mcp"},
                SERVER: {"url": URL},
            }
        })


if __name__ == "__main__":
    unittest.main()
