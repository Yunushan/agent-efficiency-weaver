import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentweaver.cli import main
from agentweaver.integrations import load_integrations, load_operating_systems

ROOT = Path(__file__).resolve().parents[1]


class InventoryCliTests(unittest.TestCase):
    def test_inventory_json_has_schema_and_all_integrations(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["inventory", "--root", str(ROOT), "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["schema_version"], 1)
        integrations = load_integrations(ROOT)
        self.assertEqual(len(data["integrations"]), len(integrations))
        self.assertEqual(len(data["operating_systems"]), len(load_operating_systems(ROOT)))
        self.assertEqual(
            {item["id"] for item in data["integrations"]},
            set(integrations),
        )
        for item in data["integrations"]:
            expected = integrations[item["id"]]
            self.assertEqual(item["kind"], expected.kind)
            self.assertEqual([surface["id"] for surface in item["surfaces"]], list(expected.surface_ids()))

    def test_inventory_text_is_human_readable(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["inventory", "--root", str(ROOT), "--format", "text"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("schema_version: 1", text)
        integrations = load_integrations(ROOT)
        for kind, label in (("agent", "agents"), ("provider", "providers"), ("hybrid", "hybrids")):
            self.assertIn(f"{label}: {sum(item.kind == kind for item in integrations.values())}", text)
        self.assertIn("operating_systems: android, bsd, ios, linux, macos, solaris, windows", text)
        self.assertIn("openrouter\tprovider\tgateway", text)
        self.assertIn("lm-studio\thybrid\tdesktop", text)


if __name__ == "__main__":
    unittest.main()
