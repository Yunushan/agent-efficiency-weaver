import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]


class InventoryCliTests(unittest.TestCase):
    def test_inventory_json_has_schema_and_all_integrations(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["inventory", "--root", str(ROOT), "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(len(data["integrations"]), 20)
        self.assertEqual(len(data["operating_systems"]), 7)
        self.assertEqual(
            {item["id"] for item in data["integrations"]},
            {
                "kilo", "opencode", "cline", "copilot", "cursor", "openhands",
                "zed", "junie", "goose", "kiro", "antigravity", "pi", "codex",
                "devin", "abacus", "qoder", "amp", "claude-code", "aider",
                "openrouter",
            },
        )

    def test_inventory_text_is_human_readable(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["inventory", "--root", str(ROOT), "--format", "text"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("schema_version: 1", text)
        self.assertIn("agents: 19", text)
        self.assertIn("providers: 1", text)
        self.assertIn("operating_systems: android, bsd, ios, linux, macos, solaris, windows", text)
        self.assertIn("openrouter\tprovider\tgateway", text)


if __name__ == "__main__":
    unittest.main()
