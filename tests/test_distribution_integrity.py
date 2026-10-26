import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_distribution import ROOT, distribution


class DistributionIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.name, self.version = distribution._project_identity()
        self.expected = distribution._canonical_hashes()
        self.modules = distribution._module_names()
        self.source_prefix = f'{self.name}-{self.version}/'
        assets = {relative: (ROOT / relative).read_bytes() for relative in self.expected}
        package = {relative: (ROOT / 'src/agentweaver' / relative).read_bytes()
                   for relative in [*self.modules, 'py.typed']}
        source = dict(assets)
        source.update({f'src/agentweaver/{relative}': content for relative, content in package.items()})
        source.update({relative: (ROOT / relative).read_bytes()
                       for relative in ('LICENSE', 'build_helpers.py', 'pyproject.toml')})
        source['PKG-INFO'] = b'Metadata-Version: 2.4\n'
        self.sdist = {self.source_prefix + relative: content for relative, content in source.items()}
        self.wheel = {f'agentweaver/_data/{relative}': content for relative, content in assets.items()}
        self.wheel.update({f'agentweaver/{relative}': content for relative, content in package.items()})
        self.wheel[f'{self.name}-{self.version}.dist-info/licenses/LICENSE'] = (ROOT / 'LICENSE').read_bytes()

    def check_sdist(self, files):
        distribution._check_sdist(files, self.name, self.version, self.expected, self.modules)

    def check_wheel(self, files):
        distribution._check_wheel(files, self.name, self.version, self.expected, self.modules)

    def test_canonical_package_and_build_files_pass(self):
        self.check_sdist(self.sdist)
        self.check_wheel(self.wheel)

    def test_runtime_module_bytes_must_match_checkout(self):
        cases = [(self.sdist, self.source_prefix + 'src/agentweaver/cli.py', self.check_sdist),
                 (self.wheel, 'agentweaver/cli.py', self.check_wheel)]
        for original, path, check in cases:
            files = dict(original)
            files[path] += b'\n# altered runtime module\n'
            with self.subTest(path=path), self.assertRaisesRegex(distribution.CheckError, 'hash mismatch'):
                check(files)

    def test_typing_marker_is_required_and_matches_checkout(self):
        cases = [(self.sdist, self.source_prefix + 'src/agentweaver/py.typed', self.check_sdist),
                 (self.wheel, 'agentweaver/py.typed', self.check_wheel)]
        for original, path, check in cases:
            for content in (None, b'altered marker\n'):
                files = dict(original)
                if content is None:
                    del files[path]
                else:
                    files[path] = content
                with self.subTest(path=path, content=content), self.assertRaises(distribution.CheckError):
                    check(files)

    def test_altered_source_build_inputs_fail_before_build_or_install(self):
        for relative in ('build_helpers.py', 'pyproject.toml'):
            files = dict(self.sdist)
            files[self.source_prefix + relative] += b'\n# altered build input\n'
            error = io.StringIO()
            with (self.subTest(relative=relative), redirect_stderr(error),
                  mock.patch.object(distribution, '_read_tar', return_value=files),
                  mock.patch.object(distribution, '_read_wheel', return_value=self.wheel),
                  mock.patch.object(distribution, '_extract_sdist') as extract,
                  mock.patch.object(distribution, '_run') as run,
                  mock.patch.object(distribution, '_install') as install):
                self.assertEqual(distribution.main(['--sdist', 'synthetic.tar.gz', '--wheel', 'synthetic.whl']), 1)
                self.assertIn('hash mismatch', error.getvalue())
                extract.assert_not_called()
                run.assert_not_called()
                install.assert_not_called()

    def test_installed_smoke_uses_canonical_allocated_temporary_root(self):
        with tempfile.TemporaryDirectory() as directory:
            canonical = Path(directory).resolve()
            alias = canonical.parent / (canonical.name + "-system-temp-alias")
            original_resolve = Path.resolve

            def resolve_alias(path, *args, **kwargs):
                return canonical if path == alias else original_resolve(path, *args, **kwargs)

            def install(_wheel, base, label):
                self.assertEqual(base, canonical)
                return "fixture-python", base / f"{label}-cwd", {}

            allocation = mock.MagicMock()
            allocation.__enter__.return_value = str(alias)
            rebuilt = canonical / "sdist-wheel/rebuilt.whl"
            with (redirect_stdout(io.StringIO()),
                  mock.patch.object(distribution.tempfile, "TemporaryDirectory", return_value=allocation),
                  mock.patch.object(Path, "resolve", new=resolve_alias),
                  mock.patch.object(distribution, "_read_tar", return_value=self.sdist),
                  mock.patch.object(distribution, "_read_wheel", return_value=self.wheel),
                  mock.patch.object(distribution, "_extract_sdist"),
                  mock.patch.object(distribution, "_run"),
                  mock.patch.object(distribution, "_single", return_value=rebuilt),
                  mock.patch.object(distribution, "_install", side_effect=install),
                  mock.patch.object(distribution, "_smoke") as smoke):
                result = distribution.main(["--sdist", "synthetic.tar.gz", "--wheel", "synthetic.whl"])
            self.assertEqual(result, 0)
            self.assertEqual([call.args[1] for call in smoke.call_args_list],
                             [canonical / "original-cwd", canonical / "rebuilt-cwd"])


if __name__ == '__main__':
    unittest.main()
