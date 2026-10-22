import errno
import hashlib
import inspect
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from agentweaver.artifacts import RenderedArtifact, write_artifacts
from agentweaver.config import WeaverError

MANIFEST = ".agentweaver-manifest.json"


def artifact(path, text="content\n", kind="test"):
    return RenderedArtifact(PurePosixPath(path), text.encode("utf-8"), kind)


def manifest_path(root):
    return Path(root) / MANIFEST


def make_symlink(case, target, link, *, target_is_directory=False):
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except OSError as exc:
        if (
            isinstance(exc, PermissionError)
            or getattr(exc, "winerror", None) == 1314
            or exc.errno in (errno.EACCES, errno.EPERM)
        ):
            case.skipTest("symbolic link creation requires unavailable permission")
        raise


def make_junction(case, junction, target):
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).lower()
        if "privilege" in message or "access is denied" in message:
            case.skipTest("junction creation requires unavailable permission")
        case.fail(f"mklink failed: {result.stderr or result.stdout}")


class ArtifactWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def assert_tree_empty(self):
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_writes_utf8_lf_bytes_and_manifest(self):
        content = "grüße\n世界\n".encode("utf-8")
        item = RenderedArtifact(PurePosixPath("nested/grüße.txt"), content, "text")
        result = write_artifacts(self.root, [item])
        self.assertEqual(result, [self.root / "nested" / "grüße.txt"])
        self.assertEqual(result[0].read_bytes(), content)
        data = json.loads(manifest_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["generator"], "agentweaver")
        self.assertEqual(
            data["entries"],
            [
                {
                    "path": "nested/grüße.txt",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "kind": "text",
                }
            ],
        )
        self.assertNotIn(b"\r", manifest_path(self.root).read_bytes())

    def test_deterministic_repeated_write_with_force(self):
        item = artifact("a.txt", "same\n")
        write_artifacts(self.root, [item])
        first = manifest_path(self.root).read_bytes()
        write_artifacts(self.root, [item], force=True)
        self.assertEqual(manifest_path(self.root).read_bytes(), first)
        self.assertEqual((self.root / "a.txt").read_bytes(), b"same\n")

    def test_rejects_invalid_content_before_writing(self):
        cases = [
            b"line\r\n",
            b"\xff\xfe",
        ]
        for content in cases:
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    items = [
                        artifact("good.txt"),
                        RenderedArtifact(PurePosixPath("bad.txt"), content, "text"),
                    ]
                    with self.assertRaises(WeaverError):
                        write_artifacts(root, items)
                    self.assertEqual(list(root.rglob("*")), [])

    def test_rejects_unsafe_paths_before_writing(self):
        cases = [
            "/etc/passwd",
            "//server/share/file",
            "C:/temp/file",
            "a\\b",
            "a/../b",
            "../b",
            "a/b?.txt",
            "a/b*.txt",
            "a/b<c",
            "a/b>c",
            "a/b|c",
            "a/b:c",
            'a/b"c',
            "a/CON",
            "a/con.txt",
            "a/COM1.log",
            "a/COM¹.log",
            "a/lpt².txt",
            "a/LPT9",
            "a/AUX.txt",
            "a/NUL",
            "a/PRN.txt",
            "a/CLOCK$",
            "a/trailing.",
            "a/trailing ",
            "a/control\x01name",
            "a/surrogate\ud800",
            ".",
            "",
            MANIFEST,
            "nested/.AGENTWEAVER-MANIFEST.JSON",
            "...",
        ]
        for value in cases:
            with self.subTest(path=value):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    items = [artifact("safe.txt"), artifact(value)]
                    with self.assertRaises(WeaverError):
                        write_artifacts(root, items)
                    self.assertEqual(list(root.rglob("*")), [])

    def test_rejects_casefold_and_ancestor_conflicts_before_writing(self):
        cases = [
            [artifact("A.txt"), artifact("a.txt")],
            [artifact("a"), artifact("a/b.txt")],
            [artifact("a/b.txt"), artifact("A/B.txt")],
        ]
        for items in cases:
            with self.subTest(items=items):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    with self.assertRaises(WeaverError):
                        write_artifacts(root, items)
                    self.assertEqual(list(root.rglob("*")), [])

    def test_casefold_alias_cannot_replace_prior_manifest_ownership(self):
        write_artifacts(self.root, [artifact('Original.txt')])
        before = manifest_path(self.root).read_bytes()
        (self.root / 'Original.txt').unlink()
        with self.assertRaises(WeaverError):
            write_artifacts(self.root, [artifact('original.txt')], force=True)
        self.assertFalse((self.root / 'original.txt').exists())
        self.assertEqual(manifest_path(self.root).read_bytes(), before)

    def test_multifile_preflight_prevents_partial_touches(self):
        (self.root / "existing.txt").write_text("keep\n", encoding="utf-8")
        items = [artifact("new/first.txt"), artifact("existing.txt")]
        with self.assertRaises(WeaverError):
            write_artifacts(self.root, items)
        self.assertFalse((self.root / "new").exists())
        self.assertEqual((self.root / "existing.txt").read_text(encoding="utf-8"), "keep\n")
        self.assertFalse(manifest_path(self.root).exists())

    def test_simulated_permission_failure_prevents_all_writes(self):
        items = [artifact("one.txt"), artifact("two/three.txt")]
        with mock.patch("agentweaver.artifacts._is_writable", return_value=False):
            with self.assertRaises(WeaverError):
                write_artifacts(self.root, items)
        self.assert_tree_empty()

    def test_force_replaces_only_owned_unchanged_files(self):
        write_artifacts(self.root, [artifact("a.txt", "v1\n")])
        write_artifacts(self.root, [artifact("a.txt", "v2\n")], force=True)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "v2\n")
        with self.assertRaises(WeaverError):
            write_artifacts(self.root, [artifact("a.txt", "v3\n")])
        (self.root / "a.txt").write_text("manually edited\n", encoding="utf-8")
        with self.assertRaises(WeaverError):
            write_artifacts(self.root, [artifact("a.txt", "v4\n")], force=True)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "manually edited\n")
        (self.root / "unmanaged.txt").write_text("foreign\n", encoding="utf-8")
        with self.assertRaises(WeaverError):
            write_artifacts(self.root, [artifact("unmanaged.txt", "ours\n")], force=True)
        self.assertEqual(
            (self.root / "unmanaged.txt").read_text(encoding="utf-8"), "foreign\n"
        )

    def test_manifest_retains_untouched_entries(self):
        write_artifacts(self.root, [artifact("a.txt", "a1\n"), artifact("b.txt", "b1\n")])
        write_artifacts(self.root, [artifact("a.txt", "a2\n")], force=True)
        self.assertEqual((self.root / "b.txt").read_text(encoding="utf-8"), "b1\n")
        data = json.loads(manifest_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual([entry["path"] for entry in data["entries"]], ["a.txt", "b.txt"])
        self.assertEqual(
            data["entries"][0]["sha256"],
            hashlib.sha256(b"a2\n").hexdigest(),
        )
        self.assertEqual(
            data["entries"][1]["sha256"],
            hashlib.sha256(b"b1\n").hexdigest(),
        )

    def test_manifest_conflicts_with_new_artifacts_are_rejected(self):
        write_artifacts(self.root, [artifact("a/b.txt", "old\n")])
        with self.assertRaises(WeaverError):
            write_artifacts(self.root, [artifact("a", "new\n")])
        self.assertEqual((self.root / "a" / "b.txt").read_text(encoding="utf-8"), "old\n")

    def test_deeply_nested_manifest_raises_safe_error_before_any_write(self):
        manifest_path(self.root).write_text('{"x":' + '[' * 2000 + '0' + ']' * 2000 + '}', encoding='utf-8')
        before = manifest_path(self.root).read_bytes()
        with mock.patch('agentweaver.artifacts.json.loads', side_effect=RecursionError('NEVER-ECHO')):
            with self.assertRaises(WeaverError) as raised:
                write_artifacts(self.root, [artifact('new.txt')])
        self.assertNotIn('NEVER-ECHO', str(raised.exception))
        self.assertFalse((self.root / 'new.txt').exists())
        self.assertEqual(manifest_path(self.root).read_bytes(), before)

    def test_rejects_invalid_manifest_fields(self):
        cases = [
            "not json",
            json.dumps({"schema_version": 2, "generator": "agentweaver", "entries": []}),
            json.dumps({"schema_version": 1, "generator": "other", "entries": []}),
            json.dumps({"schema_version": 1, "generator": "agentweaver", "entries": {}}),
            json.dumps(
                {
                    "schema_version": 1,
                    "generator": "agentweaver",
                    "entries": [{"path": "a.txt", "sha256": "abc"}],
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "generator": "agentweaver",
                    "entries": [{"path": "../a.txt", "sha256": "0" * 64}],
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "generator": "agentweaver",
                    "entries": [
                        {"path": "a.txt", "sha256": "0" * 64, "extra": True}
                    ],
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "generator": "agentweaver",
                    "entries": [],
                    "extra": True,
                }
            ),
        ]
        for text in cases:
            with self.subTest(manifest=text):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    manifest_path(root).write_text(text, encoding="utf-8")
                    with self.assertRaises(WeaverError):
                        write_artifacts(root, [artifact("out.txt")])
                    self.assertFalse((root / "out.txt").exists())

    def test_manifest_reserved_when_existing_as_directory(self):
        manifest_path(self.root).mkdir()
        with self.assertRaises(WeaverError):
            write_artifacts(self.root, [artifact("out.txt")])
        self.assertFalse((self.root / "out.txt").exists())

    def test_missing_root_chain_is_created_with_spaces_and_unicode(self):
        root = self.root / "generated output" / "nested" / "grüße"
        result = write_artifacts(
            root, [artifact("one.txt"), artifact("sub/two.txt", "two\n")]
        )
        self.assertEqual(
            result,
            [root / "one.txt", root / "sub" / "two.txt"],
        )
        self.assertEqual((root / "one.txt").read_bytes(), b"content\n")
        self.assertEqual((root / "sub" / "two.txt").read_bytes(), b"two\n")
        self.assertTrue(manifest_path(root).is_file())

    def test_root_must_be_a_directory(self):
        root_file = self.root / "file"
        root_file.write_text("x", encoding="utf-8")
        with self.assertRaises(WeaverError):
            write_artifacts(root_file, [artifact("out.txt")])
        self.assertFalse((self.root / "out.txt").exists())

    def test_relative_root_is_supported_without_resolving_links(self):
        with mock.patch("pathlib.Path.cwd", return_value=self.root):
            result = write_artifacts("relative output/grüße", [artifact("out.txt")])
        expected = self.root / "relative output" / "grüße"
        self.assertEqual(result, [expected / "out.txt"])
        self.assertEqual((expected / "out.txt").read_bytes(), b"content\n")

    def test_root_parent_traversal_is_rejected_without_creation(self):
        root = self.root / "container" / ".." / "escaped"
        with self.assertRaises(WeaverError) as caught:
            write_artifacts(root, [artifact("out.txt")])
        self.assertIn("..", str(caught.exception))
        self.assertFalse((self.root / "container").exists())
        self.assertFalse((self.root / "escaped").exists())

    def test_missing_root_not_created_for_invalid_artifact_set(self):
        root = self.root / "new root"
        items = [artifact("valid.txt"), artifact("bad?.txt")]
        with self.assertRaises(WeaverError):
            write_artifacts(root, items)
        self.assertFalse(root.exists())

    def test_missing_root_not_created_for_conflicting_artifact_set(self):
        root = self.root / "new root"
        items = [artifact("A.txt"), artifact("a.txt")]
        with self.assertRaises(WeaverError):
            write_artifacts(root, items)
        self.assertFalse(root.exists())

    def test_missing_root_not_created_when_permission_denied(self):
        root = self.root / "new root"
        with mock.patch("agentweaver.artifacts._is_writable", return_value=False):
            with self.assertRaises(WeaverError) as caught:
                write_artifacts(root, [artifact("out.txt")])
        self.assertIn("writable", str(caught.exception))
        self.assertFalse(root.exists())

    def test_existing_root_ancestor_file_is_rejected(self):
        blocker = self.root / "blocker"
        blocker.write_text("x", encoding="utf-8")
        root = blocker / "output"
        with self.assertRaises(WeaverError) as caught:
            write_artifacts(root, [artifact("out.txt")])
        self.assertIn("ancestor", str(caught.exception))
        self.assertFalse((blocker / "output").exists())

    def test_symlink_ancestor_of_missing_root_is_rejected(self):
        outside = self.root / "outside-root"
        outside.mkdir()
        link = self.root / "root-link"
        make_symlink(self, outside, link, target_is_directory=True)
        root = link / "container" / "output"
        with self.assertRaises(WeaverError) as caught:
            write_artifacts(root, [artifact("out.txt")])
        self.assertIn("link", str(caught.exception).lower())
        self.assertFalse((outside / "container").exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction ancestor test")
    def test_windows_junction_ancestor_of_missing_root_is_rejected(self):
        outside = self.root / "outside-root"
        outside.mkdir()
        junction = self.root / "junction"
        make_junction(self, junction, outside)
        root = junction / "container" / "output"
        with self.assertRaises(WeaverError) as caught:
            write_artifacts(root, [artifact("out.txt")])
        self.assertIn("link", str(caught.exception).lower())
        self.assertFalse((outside / "container").exists())

    def test_dry_run_requires_no_writer_call(self):
        item = artifact("planned.txt")
        self.assertNotIn("dry_run", inspect.signature(write_artifacts).parameters)
        self.assertFalse((self.root / "planned.txt").exists())
        self.assertFalse(manifest_path(self.root).exists())
        self.assertEqual(item.content, b"content\n")

    def test_empty_artifacts_does_not_create_manifest(self):
        self.assertEqual(write_artifacts(self.root, []), [])
        self.assertFalse(manifest_path(self.root).exists())

    def test_target_symlink_is_rejected(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        link = self.root / "link.txt"
        make_symlink(self, outside, link)
        with self.assertRaises(WeaverError) as caught:
            write_artifacts(self.root, [artifact("link.txt")])
        self.assertIn("link", str(caught.exception).lower())
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
        self.assertFalse(manifest_path(self.root).exists())

    def test_intermediate_symlink_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "linked"
        make_symlink(self, outside, link, target_is_directory=True)
        with self.assertRaises(WeaverError) as caught:
            write_artifacts(self.root, [artifact("linked/out.txt")])
        self.assertIn("link", str(caught.exception).lower())
        self.assertFalse((outside / "out.txt").exists())

    def test_manifest_symlink_is_rejected(self):
        outside = self.root / "outside-manifest.json"
        outside.write_text("{}", encoding="utf-8")
        make_symlink(self, outside, manifest_path(self.root))
        with self.assertRaises(WeaverError):
            write_artifacts(self.root, [artifact("out.txt")])
        self.assertFalse((self.root / "out.txt").exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "{}")

    def test_failed_write_cleans_temporary_file_and_redacts_error(self):
        with mock.patch(
            "agentweaver.artifacts.os.replace", side_effect=OSError("secret detail")
        ):
            with self.assertRaises(WeaverError) as caught:
                write_artifacts(self.root, [artifact("out.txt")])
        message = str(caught.exception)
        self.assertNotIn("secret detail", message)
        self.assertNotIn(str(self.root), message)
        self.assertEqual(list(self.root.glob("*.agentweaver.tmp")), [])
        self.assertEqual(list(self.root.glob(".*.agentweaver.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_windows_junction_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        junction = self.root / "junction"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest("junction creation requires unavailable permission")
        with self.assertRaises(WeaverError) as caught:
            write_artifacts(self.root, [artifact("junction/out.txt")])
        self.assertIn("link", str(caught.exception).lower())
        self.assertFalse((outside / "out.txt").exists())

    @unittest.skipUnless(os.name == "nt", "Windows reparse attribute test")
    def test_root_junction_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        junction = self.root / "junction"
        make_junction(self, junction, outside)
        with self.assertRaises(WeaverError):
            write_artifacts(junction, [artifact("out.txt")])
        self.assertFalse((outside / "out.txt").exists())


if __name__ == "__main__":
    unittest.main()
