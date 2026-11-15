import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cli_exports import invoke

from agentweaver.codex_models import inspect_codex_models, validate_codex_selection
from agentweaver.config import WeaverError


def entry(entry_id, model=None, *, hidden=False, efforts=None, default=None):
    row = {"id": entry_id, "model": model or entry_id, "hidden": hidden}
    if efforts is not None:
        row["supportedReasoningEfforts"] = [{"reasoningEffort": effort} for effort in efforts]
    if default is not None:
        row["defaultReasoningEffort"] = default
    return row


def page(*rows, cursor=None):
    return {"result": {"data": list(rows), "nextCursor": cursor}}


class CodexModelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "catalog.json"

    def save(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_later_page_selection_agrees_with_agent_validator(self):
        self.save({"pages": [
            page(entry("alias-one", "first", efforts=["low"], default="low"), cursor="next"),
            page(entry("alias-two", "second", efforts=["high", "ultra", "future-effort"], default="high"),
                 entry("internal", "second", hidden=True)),
        ]})
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with mock.patch("socket.create_connection", side_effect=AssertionError("network")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process")):
            status, output, error = invoke("codex-models", "--input", str(self.path),
                                           "--model", "second", "--reasoning-effort", "ultra")
        self.assertEqual((status, error), (0, ""))
        report = json.loads(output)
        self.assertEqual(report["pages_read"], 2)
        self.assertFalse(report["more_pages_reported"])
        self.assertEqual(report["catalog_completeness"], "unknown")
        self.assertEqual(report["snapshot_sha256"], digest)
        self.assertEqual(report["models"][1]["supported_efforts"], ["high", "ultra", "future-effort"])
        self.assertEqual(report["models"][1]["default_effort"], "high")
        self.assertIsNone(report["models"][2]["supported_efforts"])
        self.assertEqual(report["selection"], {"model": "second", "model_reasoning_effort": "ultra"})
        self.assertEqual(validate_codex_selection(self.path, "second", "ultra"), digest)
        for model, effort in (("alias-two", None), ("second", "low"), ("second", "future-effort"), (None, "high")):
            with self.subTest(model=model, effort=effort), self.assertRaises(WeaverError):
                inspect_codex_models(self.path, model=model, effort=effort)

    def test_partial_and_legacy_replies_preserve_unknowns(self):
        self.save(page(entry("first", efforts=[], default="medium"), cursor="omitted-page"))
        report = inspect_codex_models(self.path)
        self.assertTrue(report["more_pages_reported"])
        self.assertIsNone(report["selection"])
        self.assertNotIn("omitted-page", json.dumps(report))
        self.assertEqual(report["models"][0]["supported_efforts"], [])
        self.save({"result": {"data": [entry("first")]}})
        report = inspect_codex_models(self.path, model="first")
        self.assertIsNone(report["more_pages_reported"])
        self.assertIsNone(report["models"][0]["supported_efforts"])
        self.assertEqual(report["selection"], {"model": "first"})
        with self.assertRaises(WeaverError):
            validate_codex_selection(self.path, "first", "high")

    def test_rejects_invalid_pages_and_ambiguous_selection(self):
        for pages in ([], [page(), page()], [page(cursor="repeat"), page(cursor="repeat")],
                      [page(entry("dup"), cursor="next"), page(entry("dup"))],
                      [page(cursor="next"), {"error": {"code": -32600}}],
                      [page(entry("model", efforts=["high", "high"]))]):
            with self.subTest(pages=pages), self.assertRaises(WeaverError):
                self.save({"pages": pages})
                inspect_codex_models(self.path)
        self.save({"pages": [page(entry("a", "same"), cursor="next"), page(entry("b", "same"))]})
        self.assertEqual(len(inspect_codex_models(self.path)["models"]), 2)
        with self.assertRaises(WeaverError):
            validate_codex_selection(self.path, "same", None)

    def test_metadata_screening_and_cli_errors_do_not_echo_input(self):
        unsafe = page(entry("first"))
        unsafe["result"]["api_key"] = "NEVER-ECHO"
        self.save(unsafe)
        status, output, error = invoke("codex-models", "--input", str(self.path))
        self.assertEqual((status, output), (2, ""))
        self.assertNotIn("NEVER-ECHO", error)
        self.assertNotIn(str(self.path), error)
