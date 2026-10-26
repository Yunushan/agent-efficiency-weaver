import ast
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class InstallerContractTests(unittest.TestCase):
    def test_powershell_uses_normal_offline_install_and_default_validation(self):
        text = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        self.assertIn("$PSScriptRoot", text)
        self.assertIn("$env:PYTHON", text)
        self.assertIn("$projectRoot", text)
        self.assertIn("'--no-deps'", text)
        self.assertIn("'--no-build-isolation'", text)
        self.assertIn("'--user'", text)
        self.assertIn("@pipArgs", text)
        self.assertNotIn("'-e'", text)
        self.assertNotIn(" install -e", text)
        self.assertRegex(text, r"-m agentweaver validate(?:\s|$)")
        self.assertNotIn("--root", text)
        self.assertNotIn("sudo", text.lower())

    def test_posix_uses_normal_offline_install_and_default_validation(self):
        text = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        self.assertIn("CDPATH=", text)
        self.assertIn("${PYTHON:-python3}", text)
        self.assertIn("$project_root", text)
        self.assertIn("--no-deps", text)
        self.assertIn("--no-build-isolation", text)
        self.assertIn("--user", text)
        self.assertNotIn(" -e ", text)
        self.assertNotIn("pip install -e", text)
        self.assertRegex(text, r"-m agentweaver validate(?:\s|$)")
        self.assertNotIn("--root", text)
        self.assertNotIn("sudo", text.lower())
        self.assertNotIn("bash", text.splitlines()[0].lower())


class VerificationScriptTests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location("verify_platform_under_test", ROOT / "scripts/verify-platform.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_source_validation_uses_explicit_root_and_child_pythonpath(self):
        module = self.load_module()
        root = ROOT.resolve()
        calls = []

        def fake_run(command, **kwargs):
            calls.append((list(command), kwargs))
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        facts = {"system": "Linux", "release": "fixture", "version": "fixture", "machine": "x86_64"}
        with mock.patch("agentweaver.platforms.collect_host_facts", return_value=facts), \
             mock.patch.object(module.platform, "machine", return_value="x86_64"), \
             mock.patch.object(module.subprocess, "run", side_effect=fake_run):
            report = module.build_report(root, 30)

        self.assertEqual(len(calls), 2)
        command, environment = calls[0]
        self.assertEqual(
            command,
            [sys.executable, "-m", "agentweaver", "validate", "--root", str(root)],
        )
        self.assertIn(str(root / "src"), environment["env"]["PYTHONPATH"].split(os.pathsep))
        source = (ROOT / "scripts/verify-platform.py").read_text(encoding="utf-8")
        self.assertNotIn("from agentweaver.platforms import", source.split("def build_report")[0])
        self.assertEqual([check["status"] for check in report["checks"]], ["passed", "passed"])
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["host"]["detected_os"], "linux")

    def test_no_module_import_follows_sys_path_mutation(self):
        source = (ROOT / "scripts/verify-platform.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        import_lines = [
            node.lineno
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        mutation_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path"
        ]
        self.assertTrue(mutation_lines)
        self.assertTrue(import_lines)
        self.assertLess(max(import_lines), min(mutation_lines))


if __name__ == "__main__":
    unittest.main()
