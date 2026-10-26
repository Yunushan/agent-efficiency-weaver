"""Readiness scores require all real gates, tools and unchanged source inputs."""

import importlib.util
import io
import json
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("readiness_under_test", ROOT / "scripts/check-readiness.py")
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


class ReadinessTests(unittest.TestCase):
    def report(self, statuses, tools=None, snapshots=None):
        if tools is None:
            tools = [{"status": "passed"}]
        outcomes = [{"name": name, "status": status} for (name, _), status in zip(readiness.GATES, statuses)]
        with mock.patch.object(readiness, "input_hashes", side_effect=snapshots or [{"a": "hash"}] * 2), \
             mock.patch.object(readiness, "tool_versions", return_value=tools), \
             mock.patch.object(readiness, "run_gate", side_effect=outcomes) as runner:
            report = readiness.build_report(ROOT, 10)
        return report, runner

    def test_full_score_requires_all_six_gates(self):
        report, runner = self.report(["passed"] * 6)
        self.assertEqual(report["score"], 100)
        self.assertTrue(report["ready"])
        self.assertEqual(report["scope"], "offline-release-readiness")
        self.assertEqual(runner.call_count, 6)
        self.assertIn("pending separate manual evidence", " ".join(report["limitations"]))

    def test_failed_timed_out_or_unavailable_gate_cannot_score_full(self):
        for status in ("failed", "timeout", "error", "not_run"):
            with self.subTest(status=status):
                report, _ = self.report(["passed"] * 5 + [status])
                self.assertEqual(report["score"], 83)
                self.assertFalse(report["ready"])

    def test_missing_tools_and_changing_inputs_leave_score_unknown(self):
        report, runner = self.report([], tools=[{"status": "unavailable"}])
        self.assertIsNone(report["score"])
        self.assertFalse(report["ready"])
        runner.assert_not_called()
        self.assertTrue(all(check["status"] == "not_run" for check in report["checks"]))
        report, _ = self.report(["passed"] * 6, snapshots=[{"a": "before"}, {"a": "after"}])
        self.assertIsNone(report["score"])
        self.assertFalse(report["inputs_unchanged"])

    def test_gate_failures_and_timeouts_are_recorded_without_private_logs(self):
        for outcome, expected in ((subprocess.CompletedProcess([], 2), "failed"),
                                  (subprocess.TimeoutExpired("private-path", 1), "timeout"),
                                  (OSError("private-path"), "error")):
            with self.subTest(expected=expected), mock.patch.object(readiness.subprocess, "run") as run:
                if isinstance(outcome, Exception):
                    run.side_effect = outcome
                else:
                    run.return_value = outcome
                result = readiness.run_gate(ROOT, "test", ("-m", "unittest"), 1)
                self.assertEqual(result["status"], expected)
                self.assertNotIn("private-path", json.dumps(result))
                self.assertEqual(run.call_args.kwargs["env"]["PIP_NO_INDEX"], "1")
                self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def test_hashes_include_release_inputs_and_exclude_generated_files(self):
        hashes = readiness.input_hashes(ROOT)
        for name in ("build_helpers.py", "MANIFEST.in", "CONTRIBUTING.md", ".github/workflows/ci.yml",
                     "scripts/check-readiness.py", "tests/test_readiness.py"):
            self.assertIn(name, hashes)
        self.assertFalse(any(Path(name).is_absolute() or name.startswith("generated/") for name in hashes))

    def test_unit_gate_records_counts_and_refuses_empty_or_missing_discovery(self):
        for summary, expected in (("Ran 12 tests in 0.1s\n\nOK (skipped=2)\n", "passed"),
                                  ("Ran 0 tests in 0.0s\n\nOK\n", "error"), ("", "error")):
            completed = subprocess.CompletedProcess([], 0, stderr=summary)
            with self.subTest(summary=summary), mock.patch.object(readiness.subprocess, "run", return_value=completed):
                result = readiness.run_gate(ROOT, "unittest", ("-m", "unittest"), 1)
            self.assertEqual(result["status"], expected)
            if expected == "passed":
                self.assertEqual(result["tests_run"], 12)
                self.assertEqual(result["tests_skipped"], 2)

    def test_missing_or_mismatched_dev_tools_are_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                '[project.optional-dependencies]\ndev = ["tool-a==1", "tool-b==2"]\n', encoding="utf-8"
            )
            with mock.patch.object(readiness.metadata, "version", side_effect=["9", readiness.metadata.PackageNotFoundError]):
                tools = readiness.tool_versions(root)
            self.assertEqual([tool["status"] for tool in tools], ["unavailable", "unavailable"])
            self.assertIsNone(tools[1]["installed"])

    def test_nonregular_and_reparse_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.mkdir()
            with self.assertRaises(ValueError):
                readiness.input_hashes(root)
            source.rmdir()
            source.write_text("public fixture", encoding="utf-8")
            original = Path.lstat

            def reparse(path):
                if path == source:
                    return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
                return original(path)

            with mock.patch.object(Path, "lstat", reparse), self.assertRaises(ValueError):
                readiness.input_hashes(root)

    def test_main_exit_status_reflects_real_report_readiness(self):
        for ready, expected in ((True, 0), (False, 1)):
            output = io.StringIO()
            with self.subTest(ready=ready), redirect_stdout(output), \
                 mock.patch.object(readiness, "build_report", return_value={"ready": ready}):
                self.assertEqual(readiness.main([]), expected)
            self.assertEqual(json.loads(output.getvalue()), {"ready": ready})
        error = io.StringIO()
        with redirect_stderr(error), mock.patch.object(readiness, "build_report", side_effect=OSError("private")):
            self.assertEqual(readiness.main([]), 2)
        self.assertNotIn("private", error.getvalue())

    def test_timeout_must_be_positive(self):
        for value in ("0", "-1", "invalid"):
            with self.subTest(value=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                readiness.main(["--timeout", value])
            self.assertEqual(result.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
