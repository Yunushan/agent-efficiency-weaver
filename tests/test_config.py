import tempfile
import unittest
from pathlib import Path
from agentweaver.config import load_json, render, write_generated, WeaverError

ROOT = Path(__file__).resolve().parents[1]

class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        self.profile = load_json(ROOT / "profiles/efficient.json")

    def test_disabled_by_default(self):
        self.assertEqual(render("kilo", self.profile, self.catalog, []), {"mcp": {}})

    def test_explicit_enable(self):
        output = render("abacus", self.profile, self.catalog, ["context7"])
        self.assertIn("context7", output["mcpServers"])

    def test_production_writes_forbidden(self):
        profile = dict(self.profile, policy={"allow_production_writes": True})
        with self.assertRaises(WeaverError):
            render("kilo", profile, self.catalog, [])

    def test_no_overwrite_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            write_generated(Path(directory), "kilo", {"mcp": {}})
            with self.assertRaises(WeaverError):
                write_generated(Path(directory), "kilo", {"mcp": {}})
