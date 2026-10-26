"""Source identity and safe output for the opt-in platform evidence script."""

import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class EvidenceScriptTestCase(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "verify_platform_evidence_under_test", ROOT / "scripts/verify-platform.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()


class SourceVersionTests(EvidenceScriptTestCase):
    def test_selected_source_version_does_not_consult_installed_metadata(self):
        (self.root / "pyproject.toml").write_text(
            '[project]\nversion = "9.8.7"\n', encoding="utf-8"
        )
        with mock.patch("importlib.metadata.version", return_value="0.1.0") as installed_version:
            self.assertEqual(self.module.package_version(self.root), ("9.8.7", "pyproject"))
        installed_version.assert_not_called()

    def test_unavailable_source_version_does_not_substitute_installed_metadata(self):
        cases = (
            None,
            "not valid TOML [",
            "[project]\n",
            "[project]\nversion = 123\n",
            '[project]\nversion = ""\n',
            '[project]\nversion = "   "\n',
        )
        for content in cases:
            with self.subTest(content=content):
                manifest = self.root / "pyproject.toml"
                if content is not None:
                    manifest.write_text(content, encoding="utf-8")
                with mock.patch("importlib.metadata.version", return_value="0.1.0") as installed_version:
                    self.assertEqual(self.module.package_version(self.root), (None, "unavailable"))
                installed_version.assert_not_called()
                if manifest.exists():
                    manifest.unlink()


class ReportOutputTests(EvidenceScriptTestCase):
    def test_creates_nested_report_without_resolving_path(self):
        output = self.root / "nested" / "evidence.json"
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("must not resolve")):
            self.assertEqual(self.module.write_report(output, {"schema_version": 1}, False), output)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"schema_version": 1})

    def test_existing_regular_report_requires_explicit_force(self):
        output = self.root / "evidence.json"
        output.write_text("original\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.module.write_report(output, {"new": True}, False)
        self.assertEqual(output.read_text(encoding="utf-8"), "original\n")
        self.module.write_report(output, {"new": True}, True)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"new": True})

    def test_relative_report_uses_current_directory(self):
        with mock.patch.object(Path, "cwd", return_value=self.root):
            output = self.module.write_report(Path("evidence.json"), {}, False)
        self.assertEqual(output, self.root / "evidence.json")

    def test_parent_traversal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not contain"):
            self.module.write_report(self.root / "nested" / ".." / "evidence.json", {}, True)
        self.assertFalse((self.root / "evidence.json").exists())

    def test_directory_output_is_rejected_even_with_force(self):
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.module.write_report(self.root, {}, True)

    def test_file_ancestor_is_rejected(self):
        ancestor = self.root / "file"
        ancestor.write_text("original", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ancestor must be a directory"):
            self.module.write_report(ancestor / "evidence.json", {}, True)
        self.assertEqual(ancestor.read_text(encoding="utf-8"), "original")

    def test_links_and_reparse_points_are_rejected_before_following(self):
        actual_lstat = os.lstat
        for ancestor in (False, True):
            for link_kind in ("symbolic-link", "reparse-point"):
                with self.subTest(ancestor=ancestor, kind=link_kind):
                    output = self.root / "linked" / "evidence.json" if ancestor else self.root / "evidence.json"
                    linked = output.parent if ancestor else output

                    def fake_lstat(path, *args, **kwargs):
                        if Path(path) == linked:
                            return SimpleNamespace(
                                st_mode=stat.S_IFLNK if link_kind == "symbolic-link" else stat.S_IFDIR,
                                st_file_attributes=0x400 if link_kind == "reparse-point" else 0,
                            )
                        return actual_lstat(path, *args, **kwargs)

                    with mock.patch.object(self.module.os, "lstat", side_effect=fake_lstat):
                        with self.assertRaisesRegex(ValueError, "links or reparse points"):
                            self.module.write_report(output, {}, True)
                    self.assertFalse(output.exists())


class ArtifactHashTests(EvidenceScriptTestCase):
    def test_packaging_and_gate_inputs_have_relative_hashes(self):
        expected_paths = (
            "build_helpers.py", "MANIFEST.in", "CONTRIBUTING.md", ".github/workflows/ci.yml",
            "scripts/check-distribution.py", "src/agentweaver/py.typed",
            "tests/fixtures/adapters/golden.json", "benchmarks/fixtures/synthetic_runs.jsonl",
        )
        for relative in expected_paths:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode("utf-8"))
        result = self.module.hash_artifacts(self.root)
        self.assertEqual([entry["path"] for entry in result], sorted(expected_paths))
        for entry in result:
            content = entry["path"].encode("utf-8")
            self.assertEqual(entry["bytes"], len(content))
            self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    unittest.main()
