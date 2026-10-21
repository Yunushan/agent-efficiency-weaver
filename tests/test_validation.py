import unittest
from pathlib import Path
from agentweaver.config import load_json, validate_catalog, validate_profile

ROOT = Path(__file__).resolve().parents[1]

class ValidationTests(unittest.TestCase):
    def test_catalog(self):
        catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        self.assertEqual(validate_catalog(catalog), [])
    def test_profiles(self):
        catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        for path in (ROOT / "profiles").glob("*.json"):
            self.assertEqual(validate_profile(load_json(path), catalog), [])
