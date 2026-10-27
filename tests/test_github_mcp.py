"""GitHub's hosted MCP routes require explicit, scoped staging choices."""
import json
import socket
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from agentweaver.adapters import render_mcp
from agentweaver.config import WeaverError, load_json, select_servers, validate_catalog
from agentweaver.integrations import load_integrations

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md"
ROUTES = {
    "github": "https://api.githubcopilot.com/mcp/",
    "github-readonly": "https://api.githubcopilot.com/mcp/readonly",
    "github-all": "https://api.githubcopilot.com/mcp/x/all",
    "github-all-readonly": "https://api.githubcopilot.com/mcp/x/all/readonly",
}


class GithubMcpTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        self.profile = load_json(ROOT / "profiles/efficient.json")
        self.integrations = load_integrations(ROOT)

    def document(self, product, profile, enabled):
        spec = self.integrations[product]
        artifact = render_mcp(spec, spec.default_surface, profile, self.catalog, enabled)[0]
        return tomllib.loads(artifact.content.decode()) if product == "codex" else json.loads(artifact.content)

    def test_default_and_all_routes_include_distinct_readonly_variants(self):
        self.assertEqual(validate_catalog(self.catalog), [])
        for name, url in ROUTES.items():
            with self.subTest(name=name):
                entry = self.catalog["servers"][name]
                self.assertEqual(set(entry), {"description", "kind", "url", "default_enabled"})
                self.assertEqual((entry["kind"], entry["url"], entry["default_enabled"]), ("remote", url, False))
                self.assertIn(SOURCE, entry["description"])
                self.assertIn("read-only" if name.endswith("readonly") else "write actions", entry["description"])

    def test_shipped_profiles_neither_allow_nor_implicitly_select_github(self):
        for path in sorted((ROOT / "profiles").glob("*.json")):
            profile = load_json(path)
            with self.subTest(profile=path.name):
                self.assertTrue(set(ROUTES).isdisjoint(profile["allowed_mcp"]))
                self.assertEqual(select_servers(profile, self.catalog, []), {})
                for name in ROUTES:
                    with self.assertRaises(WeaverError):
                        select_servers(profile, self.catalog, [name])

    def test_allowlisting_alone_does_not_enable_any_route(self):
        for name in ROUTES:
            profile = dict(self.profile, allowed_mcp=[name])
            for product, key in (("codex", "mcp_servers"), ("copilot", "servers"), ("abacus", "mcpServers")):
                with self.subTest(name=name, product=product):
                    self.assertEqual(self.document(product, profile, []), {key: {}})

    def test_explicit_selection_stages_only_the_selected_url_without_auth_fields(self):
        for name, url in ROUTES.items():
            profile = dict(self.profile, allowed_mcp=[name])
            cases = (("codex", "mcp_servers", {"url": url}),
                     ("copilot", "servers", {"type": "http", "url": url}),
                     ("abacus", "mcpServers", {"url": url}))
            for product, key, entry in cases:
                with self.subTest(name=name, product=product), mock.patch.object(
                    socket, "create_connection", side_effect=AssertionError("unexpected network")
                ):
                    self.assertEqual(self.document(product, profile, [name]), {key: {name: entry}})

    def test_non_allowlisted_route_cannot_be_selected_even_if_another_variant_is_allowed(self):
        for name in ROUTES:
            profile = dict(self.profile, allowed_mcp=[name])
            other = next(candidate for candidate in ROUTES if candidate != name)
            with self.subTest(name=name), self.assertRaises(WeaverError):
                self.document("copilot", profile, [other])


if __name__ == "__main__":
    unittest.main()
