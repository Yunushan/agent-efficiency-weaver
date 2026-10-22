import importlib.util
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_distribution_module():
    path = ROOT / "scripts/check-distribution.py"
    spec = importlib.util.spec_from_file_location("check_distribution_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


distribution = load_distribution_module()


def write_tar(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))


def write_wheel(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)

    def test_tar_and_wheel_reject_casefold_collisions(self):
        cases = [
            [("pkg/Data.txt", b"one"), ("pkg/data.txt", b"two")],
            [("Pkg/base.txt", b"one"), ("pkg/base.txt", b"two")],
            [("pkg/Foo", b"one"), ("pkg/foo", b"two")],
        ]
        for index, members in enumerate(cases):
            tar_path = self.base / f"case-{index}.tar.gz"
            wheel_path = self.base / f"case-{index}.whl"
            write_tar(tar_path, members)
            write_wheel(wheel_path, members)
            with self.subTest(members=members), self.assertRaises(distribution.CheckError):
                distribution._read_tar(tar_path)
            with self.subTest(members=members), self.assertRaises(distribution.CheckError):
                distribution._read_wheel(wheel_path)

    def test_casefold_collision_fails_before_sdist_extraction(self):
        archive = self.base / "unsafe.tar.gz"
        write_tar(
            archive,
            [("pkg/Data.txt", b"one"), ("pkg/data.txt", b"two")],
        )
        target = self.base / "extracted"
        with self.assertRaises(distribution.CheckError):
            distribution._extract_sdist(archive, target)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.base.glob("extracted/**/*")), [])

    def test_tar_and_wheel_reject_windows_reserved_names(self):
        components = [
            "CON",
            "con.txt",
            "COM1.log",
            "COM¹.log",
            "COM².log",
            "COM³.log",
            "LPT1",
            "lpt².txt",
            "LPT³.txt",
            "AUX.txt",
            "NUL",
            "PRN.txt",
            "CLOCK$",
            "CONIN$",
            "CONOUT$",
            "trailing.",
            "trailing ",
        ]
        for index, component in enumerate(components):
            members = [(f"pkg/{component}", b"data")]
            tar_path = self.base / f"reserved-{index}.tar.gz"
            wheel_path = self.base / f"reserved-{index}.whl"
            write_tar(tar_path, members)
            write_wheel(wheel_path, members)
            with self.subTest(component=component), self.assertRaises(distribution.CheckError):
                distribution._read_tar(tar_path)
            with self.subTest(component=component), self.assertRaises(distribution.CheckError):
                distribution._read_wheel(wheel_path)

    def test_tar_and_wheel_reject_windows_invalid_characters(self):
        for index, component in enumerate(("a<b", "a>b", "a:b", 'a"b', "a|b", "a?b", "a*b")):
            members = [(f"pkg/{component}", b"data")]
            tar_path = self.base / f"invalid-{index}.tar.gz"
            wheel_path = self.base / f"invalid-{index}.whl"
            write_tar(tar_path, members)
            write_wheel(wheel_path, members)
            with self.subTest(component=component), self.assertRaises(distribution.CheckError):
                distribution._read_tar(tar_path)
            with self.subTest(component=component), self.assertRaises(distribution.CheckError):
                distribution._read_wheel(wheel_path)

    def test_safe_tar_and_wheel_members_remain_readable(self):
        members = [
            (".agents/skills/security-review/SKILL.md", b"skill"),
            ("catalogs/mcp-servers.json", b"{}"),
            ("profiles/efficient.json", b"{}"),
            ("LICENSE", b"license"),
        ]
        tar_path = self.base / "safe.tar.gz"
        wheel_path = self.base / "safe.whl"
        write_tar(tar_path, members)
        write_wheel(wheel_path, members)
        self.assertEqual(distribution._read_tar(tar_path), dict(members))
        self.assertEqual(distribution._read_wheel(wheel_path), dict(members))
        target = self.base / "safe-extracted"
        distribution._extract_sdist(tar_path, target)
        for name, data in members:
            self.assertEqual((target / name).read_bytes(), data)


class BuildCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.dist = self.base / "selected-dist"

    def test_build_uses_selected_dist_and_never_reuses_stale_artifacts(self):
        name, version = distribution._project_identity()
        self.dist.mkdir()
        stale_sdist = self.dist / f"{name}-{version}.tar.gz"
        stale_wheel = self.dist / f"{name}-{version}-py3-none-any.whl"
        other_sdist = self.dist / f"{name}-0.0.1.tar.gz"
        stale_sdist.write_bytes(b"stale sdist")
        stale_wheel.write_bytes(b"stale wheel")
        other_sdist.write_bytes(b"other version")
        calls = []

        def fake_run(command, cwd, environment):
            calls.append(command)
            self.assertIn("--outdir", command)
            outdir = Path(command[command.index("--outdir") + 1])
            self.assertEqual(outdir, self.dist.resolve())
            self.assertEqual(cwd, distribution.ROOT)
            self.assertFalse(stale_sdist.exists())
            self.assertFalse(stale_wheel.exists())
            self.assertTrue(other_sdist.exists())
            (outdir / f"{name}-{version}.tar.gz").write_bytes(b"fresh sdist")
            (outdir / f"{name}-{version}-py3-none-any.whl").write_bytes(b"fresh wheel")

        with mock.patch.object(distribution, "_run", side_effect=fake_run):
            sdist, wheel = distribution._build_root(self.dist)

        self.assertEqual(len(calls), 1)
        self.assertEqual(sdist.read_bytes(), b"fresh sdist")
        self.assertEqual(wheel.read_bytes(), b"fresh wheel")
        self.assertEqual(sdist.parent, self.dist.resolve())
        self.assertEqual(wheel.parent, self.dist.resolve())
        self.assertEqual(stale_sdist.read_bytes(), b"fresh sdist")
        self.assertEqual(stale_wheel.read_bytes(), b"fresh wheel")
        self.assertTrue(other_sdist.exists())

    def test_build_without_fresh_artifacts_fails_instead_of_reusing(self):
        name, version = distribution._project_identity()
        self.dist.mkdir()
        (self.dist / f"{name}-{version}.tar.gz").write_bytes(b"stale")
        (self.dist / f"{name}-{version}-py3-none-any.whl").write_bytes(b"stale")
        with mock.patch.object(distribution, "_run", return_value=""):
            with self.assertRaises(distribution.CheckError):
                distribution._build_root(self.dist)


if __name__ == "__main__":
    unittest.main()
