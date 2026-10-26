import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from agentweaver.artifacts import RenderedArtifact
from agentweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]


def invoke(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class ExportCliTests(unittest.TestCase):
    def test_legacy_alias_and_agent_match(self):
        for agent in ('kilo', 'abacus'):
            common = ('--root', str(ROOT), '--profile', 'efficient', '--enable', 'context7', '--dry-run')
            old = invoke('render', '--platform', agent, *common)
            new = invoke('render', '--agent', agent, *common)
            self.assertEqual(old[0], 0, old[2])
            self.assertEqual(new[0], 0, new[2])
            self.assertEqual(old[1], new[1])

    def test_bad_routes_and_paths_return_errors_not_tracebacks(self):
        cases = [('--agent', 'openrouter'), ('--agent', 'aider'), ('--agent', 'missing'),
                 ('--agent', 'kilo', '--platform', 'kilo'),
                 ('--agent', 'kilo', '--surface', 'cloud'),
                 ('--agent', 'kilo', '--os', 'invalid'),
                 ('--agent', 'kilo', '--profile', '../private')]
        for args in cases:
            with self.subTest(args=args):
                code, out, err = invoke('render', '--root', str(ROOT), '--profile', 'efficient', *args, '--dry-run')
                self.assertNotEqual(code, 0)
                self.assertNotIn('Traceback', err)

    def test_dry_run_never_writes_or_calls_network_or_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'must-not-exist'
            with (patch('socket.create_connection', side_effect=AssertionError('network')),
                  patch('subprocess.run', side_effect=AssertionError('agent execution'))):
                code, out, err = invoke('render', '--root', str(ROOT), '--agent', 'kilo',
                                        '--profile', 'efficient', '--output-dir', str(target), '--dry-run')
            self.assertEqual(code, 0, err)
            self.assertFalse(target.exists())
            self.assertIn('mcp', out)

    def test_dry_run_checks_portable_artifact_safety_without_writing(self):
        good = RenderedArtifact(PurePosixPath('kilo/safe.txt'), b'safe\n', 'test')
        bad = RenderedArtifact(PurePosixPath('kilo/CON.txt'), b'bad\n', 'test')
        for artifacts in ([good, good], [good, bad]):
            with self.subTest(artifacts=artifacts), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'not-created'
                with (patch('agentweaver.cli.render_mcp', return_value=artifacts),
                      patch('agentweaver.cli.write_artifacts') as writer):
                    code, out, err = invoke('render', '--root', str(ROOT), '--agent', 'kilo',
                                            '--profile', 'efficient', '--output-dir', str(target), '--dry-run')
                self.assertNotEqual(code, 0)
                self.assertFalse(target.exists())
                writer.assert_not_called()

    def test_manifest_force_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            args = ('render', '--root', str(ROOT), '--agent', 'codex', '--profile', 'balanced',
                    '--output-dir', str(root), '--enable', 'context7')
            self.assertEqual(invoke(*args)[0], 0)
            self.assertNotEqual(invoke(*args)[0], 0)
            self.assertEqual(invoke(*args, '--force')[0], 0)
            target = root / 'codex/.codex/config.toml'
            target.write_bytes(b'changed\n')
            self.assertNotEqual(invoke(*args, '--force')[0], 0)
            self.assertEqual(target.read_bytes(), b'changed\n')
            manifest = json.loads((root / '.agentweaver-manifest.json').read_text())
            self.assertEqual(manifest['schema_version'], 1)

    def test_argument_errors_never_echo_credentials(self):
        code, out, err = invoke('provider', '--api-key', 'SECRET-DO-NOT-PRINT')
        self.assertNotEqual(code, 0)
        self.assertNotIn('SECRET-DO-NOT-PRINT', out + err)
