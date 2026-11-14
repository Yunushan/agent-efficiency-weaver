"""The opt-in OpenAI documentation MCP is distinct from a Codex server."""
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
SERVER = "openai-docs"
URL = "https://developers.openai.com/mcp"
SOURCE = "https://developers.openai.com/learn/docs-mcp"


class OpenAiDocsMcpTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        self.profile = load_json(ROOT / "profiles/efficient.json")
        self.integrations = load_integrations(ROOT)

    def document(self, product, profile, enabled):
        spec = self.integrations[product]
        artifact = render_mcp(spec, spec.default_surface, profile, self.catalog, enabled)[0]
        return tomllib.loads(artifact.content.decode()) if product == "codex" else json.loads(artifact.content)

    def test_entry_is_public_docs_only_and_not_a_codex_process(self):
        self.assertEqual(validate_catalog(self.catalog), [])
        entry = self.catalog["servers"][SERVER]
        self.assertEqual(set(entry), {"description", "kind", "url", "default_enabled"})
        self.assertEqual((entry["kind"], entry["url"], entry["default_enabled"]), ("remote", URL, False))
        self.assertIn(SOURCE, entry["description"])
        self.assertIn("documentation-only", entry["description"])
        self.assertFalse(any(item.get("kind") == "stdio" and item.get("command") == "codex"
                             and item.get("args") == ["mcp-server"] for item in self.catalog["servers"].values()))

    def test_shipped_profiles_do_not_allow_or_select_the_server(self):
        for path in sorted((ROOT / "profiles").glob("*.json")):
            profile = load_json(path)
            with self.subTest(profile=path.name):
                self.assertNotIn(SERVER, profile["allowed_mcp"])
                self.assertEqual(select_servers(profile, self.catalog, []), {})
                with self.assertRaises(WeaverError):
                    select_servers(profile, self.catalog, [SERVER])

    def test_explicit_selection_is_required_even_after_allowlisting(self):
        profile = dict(self.profile, allowed_mcp=[SERVER])
        for product, key in (("codex", "mcp_servers"), ("copilot", "servers"), ("cursor", "mcpServers")):
            with self.subTest(product=product):
                self.assertEqual(self.document(product, profile, []), {key: {}})

    def test_explicit_selection_stages_only_the_documented_endpoint(self):
        profile = dict(self.profile, allowed_mcp=[SERVER])
        cases = (("codex", "mcp_servers", {"url": URL}),
                 ("copilot", "servers", {"type": "http", "url": URL}),
                 ("cursor", "mcpServers", {"url": URL}))
        for product, key, entry in cases:
            with self.subTest(product=product), mock.patch.object(
                socket, "create_connection", side_effect=AssertionError("unexpected network")
            ):
                self.assertEqual(self.document(product, profile, [SERVER]), {key: {SERVER: entry}})


if __name__ == "__main__":
    unittest.main()
