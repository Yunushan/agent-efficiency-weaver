import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_helpers
from agentweaver.config import WeaverError
from agentweaver.resources import asset_paths, resource_root

ROOT = Path(__file__).resolve().parents[1]

CATALOGS = (
    "catalogs/custom-agents.json",
    "catalogs/integrations.json",
    "catalogs/mcp-servers.json",
    "catalogs/model-hosts.json",
    "catalogs/model-providers.json",
    "catalogs/operating-systems.json",
    "catalogs/requested-coverage.json",
)
PROFILES = (
    "profiles/balanced.json",
    "profiles/efficient.json",
    "profiles/enterprise-secure.json",
)
SKILLS = (
    ".agents/skills/benchmark-design/SKILL.md",
    ".agents/skills/gitlab-ci-review/SKILL.md",
    ".agents/skills/linux-incident-triage/SKILL.md",
    ".agents/skills/rke2-troubleshooting/SKILL.md",
    ".agents/skills/security-review/SKILL.md",
    ".agents/skills/token-efficient-retrieval/SKILL.md",
)


class FakeTraversable:
    def __init__(self, path):
        self.path = Path(path)

    def joinpath(self, *parts):
        return FakeTraversable(self.path.joinpath(*parts))

    def is_file(self):
        return self.path.is_file()

    def is_dir(self):
        return self.path.is_dir()

    def read_bytes(self):
        return self.path.read_bytes()

    def __str__(self):
        return str(self.path)


def write_assets(base):
    for relative in CATALOGS + PROFILES:
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"schema_version": 1}', encoding="utf-8")
    for relative in SKILLS:
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        skill = relative.split("/")[-2]
        target.write_text(
            f'---\nname: {skill}\ndescription: "fixture"\n---\n\n# {skill}\n',
            encoding="utf-8",
        )


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.anchor = Path(self.directory.name) / "package"
        self.data = self.anchor / "_data"
        write_assets(self.data)

    def patch_files(self, target=None):
        target = target or FakeTraversable(self.anchor)
        return mock.patch("importlib.resources.files", return_value=target)

    def test_asset_paths_are_the_exact_public_allowlist(self):
        expected = tuple(sorted(CATALOGS + PROFILES + SKILLS))
        self.assertEqual(asset_paths(), expected)
        self.assertEqual(len(set(asset_paths())), len(asset_paths()))
        self.assertEqual(build_helpers.asset_paths(), asset_paths())
        for relative in asset_paths():
            path = Path(relative)
            self.assertFalse(path.is_absolute())
            self.assertNotIn("..", path.parts)
            self.assertNotIn("\\", relative)

    def test_canonical_checkout_has_every_public_asset(self):
        for relative in asset_paths():
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())
        for relative in SKILLS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---"))
            self.assertIn("\nname:", "\n" + text.replace("\r\n", "\n"))

    def test_default_materializes_every_public_asset(self):
        with self.patch_files(), resource_root() as root:
            self.assertNotEqual(root.resolve(), ROOT.resolve())
            for relative in asset_paths():
                with self.subTest(relative=relative):
                    copied = root / relative
                    self.assertEqual(
                        copied.read_bytes(), (self.data / relative).read_bytes()
                    )
            self.assertEqual(
                (root / ".agents/skills/security-review/SKILL.md").parent.name,
                "security-review",
            )

    def test_requested_files_and_directory_prefixes_are_exclusive(self):
        requested = ["profiles/efficient.json", ".agents/skills/security-review"]
        with self.patch_files(), resource_root(paths=requested) as root:
            self.assertTrue((root / "profiles/efficient.json").is_file())
            self.assertTrue(
                (root / ".agents/skills/security-review/SKILL.md").is_file()
            )
            self.assertFalse((root / "catalogs").exists())
            self.assertFalse((root / "profiles/balanced.json").exists())
            self.assertEqual(
                sorted(
                    path.relative_to(root).as_posix()
                    for path in root.rglob("*")
                    if path.is_file()
                ),
                sorted(["profiles/efficient.json", ".agents/skills/security-review/SKILL.md"]),
            )

    def test_temporary_root_is_removed_after_context(self):
        with self.patch_files() as files, resource_root() as root:
            self.assertTrue(root.is_dir())
            files.assert_called_once_with("agentweaver")
        self.assertFalse(root.exists())

    def test_rejects_unsafe_or_unknown_resource_paths(self):
        for requested in (
            ["../LICENSE"],
            ["/etc/passwd"],
            ["catalogs\\mcp-servers.json"],
            ["catalogs/../profiles/efficient.json"],
            ["catalogs/missing.json"],
            [None],
            ["profiles/efficient.json", "profiles/efficient.json"],
        ):
            with (
                self.subTest(requested=requested),
                self.patch_files(),
                self.assertRaises(WeaverError),
                resource_root(paths=requested),
            ):
                pass

    def test_missing_bundled_asset_raises_redacted_error(self):
        (self.data / "profiles/efficient.json").unlink()
        with (
            self.patch_files(),
            self.assertRaises(WeaverError) as raised,
            resource_root(paths=["profiles/efficient.json"]),
        ):
            pass
        self.assertNotIn(str(self.data), str(raised.exception))

    def test_malformed_bundled_json_raises_redacted_error(self):
        (self.data / "catalogs/mcp-servers.json").write_text(
            '{"value": "SUPERSECRET", broken', encoding="utf-8"
        )
        with (
            self.patch_files(),
            self.assertRaises(WeaverError) as raised,
            resource_root(paths=["catalogs/mcp-servers.json"]),
        ):
            pass
        self.assertNotIn("SUPERSECRET", str(raised.exception))

    def test_deeply_nested_bundled_json_raises_redacted_error(self):
        depth = 50000
        (self.data / "catalogs/mcp-servers.json").write_text(
            '{"value": ' + "[" * depth + "]" * depth + "}", encoding="utf-8"
        )
        with (
            self.patch_files(),
            self.assertRaises(WeaverError) as raised,
            resource_root(paths=["catalogs/mcp-servers.json"]),
        ):
            pass
        self.assertNotIn("value", str(raised.exception))

    def test_malformed_bundled_skill_raises(self):
        (self.data / ".agents/skills/security-review/SKILL.md").write_text(
            "# no front matter\n", encoding="utf-8"
        )
        with (
            self.patch_files(),
            self.assertRaises(WeaverError),
            resource_root(paths=[".agents/skills/security-review/SKILL.md"]),
        ):
            pass

    def test_bundled_resource_read_failure_is_redacted(self):
        broken = mock.Mock()
        broken.joinpath.side_effect = OSError("SUPERSECRET")
        with (
            self.patch_files(broken),
            self.assertRaises(WeaverError) as raised,
            resource_root(),
        ):
            pass
        self.assertNotIn("SUPERSECRET", str(raised.exception))

    def test_explicit_override_is_used_without_copy_or_fallback(self):
        with self.patch_files(), resource_root(self.data, paths=["profiles/efficient.json"]) as root:
            self.assertEqual(root, self.data)
            self.assertTrue((root / "profiles/efficient.json").is_file())

    def test_explicit_override_missing_or_not_directory_fails_closed(self):
        missing = Path(self.directory.name) / "missing"
        with self.assertRaises(WeaverError), resource_root(missing):
            pass
        file_path = self.data / "profiles/efficient.json"
        with self.assertRaises(WeaverError), resource_root(file_path):
            pass
        with self.assertRaises(WeaverError), resource_root(
            self.data, paths=["catalogs/missing.json"]
        ):
            pass

    def test_build_copy_is_exact_clears_stale_and_leaves_source(self):
        source = Path(self.directory.name) / "source"
        write_assets(source)
        (source / "catalogs/private.json").write_text("{}", encoding="utf-8")
        build_lib = Path(self.directory.name) / "build/lib"
        stale = build_lib / "agentweaver/_data/catalogs/stale.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}", encoding="utf-8")
        before = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file()
        }
        copied = build_helpers.copy_public_assets(source, build_lib)
        self.assertFalse(stale.exists())
        resolved_build_lib = build_lib.resolve()
        self.assertEqual(
            sorted(path.relative_to(resolved_build_lib).as_posix() for path in copied),
            sorted("agentweaver/_data/" + relative for relative in asset_paths()),
        )
        for relative in asset_paths():
            self.assertEqual(
                (build_lib / "agentweaver/_data" / relative).read_bytes(),
                (source / relative).read_bytes(),
            )
        self.assertFalse((build_lib / "agentweaver/_data/catalogs/private.json").exists())
        self.assertEqual(
            before,
            {
                path.relative_to(source).as_posix(): path.read_bytes()
                for path in source.rglob("*")
                if path.is_file()
            },
        )

    def test_build_copy_refuses_missing_source_and_source_destination(self):
        source = Path(self.directory.name) / "source"
        write_assets(source)
        (source / "profiles/balanced.json").unlink()
        with self.assertRaises(RuntimeError):
            build_helpers.copy_public_assets(source, Path(self.directory.name) / "build")
        write_assets(source)
        with self.assertRaises(RuntimeError):
            build_helpers.copy_public_assets(source, source / "src/agentweaver")


if __name__ == "__main__":
    unittest.main()
