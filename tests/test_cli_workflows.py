import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_cli_exports import ROOT, invoke


class WorkflowCliTests(unittest.TestCase):
    def test_validate_checks_skills_and_required_assets(self):
        code, out, err = invoke('validate', '--root', str(ROOT))
        self.assertEqual(code, 0, err)
        self.assertIn('skill', out.lower())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('catalogs', 'profiles', '.agents'):
                shutil.copytree(ROOT / name, root / name)
            (root / '.agents/skills/security-review/SKILL.md').write_bytes(b'no front matter\n')
            self.assertNotEqual(invoke('validate', '--root', str(root))[0], 0)

    def test_doctor_is_honest_and_strict_fails_without_runtime_evidence(self):
        for target in ('windows', 'macos', 'linux', 'bsd', 'solaris', 'android', 'ios'):
            with self.subTest(target=target), patch('agentweaver.cli.detect_host_os', return_value='windows'):
                args = ('doctor', '--root', str(ROOT), '--agent', 'kilo', '--os', target, '--format', 'json')
                code, out, err = invoke(*args)
                self.assertEqual(code, 0, err)
                data = json.loads(out)
                self.assertEqual(data['schema_version'], 1)
                self.assertEqual(data['target']['os'], target)
                self.assertNotEqual(data['native_vendor']['status'], 'available')
                self.assertEqual(invoke(*args, '--strict')[0], 1)
                if target == 'ios':
                    self.assertFalse(data['workflow']['on_device_cli'])

    def test_omitted_os_uses_host_and_keeps_wsl_qualifier(self):
        with patch('agentweaver.cli.detect_host_os', return_value='linux-wsl'):
            code, out, err = invoke('doctor', '--root', str(ROOT), '--agent', 'kilo', '--format', 'json')
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data['target']['os'], 'linux')
        self.assertEqual(data['host']['qualifier'], 'wsl')
        with patch('agentweaver.cli.detect_host_os', return_value='unknown'):
            self.assertNotEqual(invoke('doctor', '--root', str(ROOT), '--agent', 'kilo')[0], 0)

    def test_bundle_and_provider_are_staging_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'new output Ω'
            code, out, err = invoke('bundle', '--root', str(ROOT), '--agent', 'aider', '--profile', 'efficient',
                                    '--skill', 'token-efficient-retrieval', '--output-dir', str(root))
            self.assertEqual(code, 0, err)
            self.assertTrue((root / 'aider/CONVENTIONS.md').is_file())
            self.assertTrue((root / 'aider/OS-HANDOFF.md').is_file())
            code, out, err = invoke('provider', '--root', str(ROOT), '--provider', 'openrouter',
                                    '--agent', 'aider', '--model', 'vendor/chosen-model', '--output-dir', str(root))
            self.assertEqual(code, 0, err)
            self.assertTrue((root / '.agentweaver-manifest.json').is_file())

    def test_custom_profile_uses_explicit_override_not_arbitrary_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'catalogs', root / 'catalogs')
            (root / 'profiles').mkdir()
            profile = json.loads((ROOT / 'profiles/efficient.json').read_text())
            profile['id'] = 'custom'
            (root / 'profiles/custom.json').write_text(json.dumps(profile), encoding='utf-8')
            code, out, err = invoke('render', '--root', str(root), '--agent', 'kilo', '--profile', 'custom', '--dry-run')
            self.assertEqual(code, 0, err)
            self.assertNotEqual(invoke('render', '--root', str(root), '--agent', 'kilo', '--profile', '../outside', '--dry-run')[0], 0)

    def test_qoder_ide_provider_is_reachable_without_fabricating_other_capabilities(self):
        common = ('--root', str(ROOT), '--agent', 'qoder', '--surface', 'ide', '--dry-run')
        code, out, err = invoke('provider', *common, '--provider', 'openrouter', '--model', 'vendor/chosen-model')
        self.assertEqual(code, 0, err)
        self.assertIn('provider-setup.md', out)
        self.assertNotEqual(invoke('render', *common, '--profile', 'efficient')[0], 0)
        self.assertNotEqual(invoke('bundle', *common, '--profile', 'efficient')[0], 0)
        self.assertNotEqual(invoke('provider', '--root', str(ROOT), '--agent', 'qoder', '--surface', 'cli',
                                   '--provider', 'openrouter', '--model', 'vendor/chosen-model', '--dry-run')[0], 0)

    def test_provider_errors_do_not_echo_model_or_keys(self):
        for model in ('SECRET?api_key=NEVER-ECHO', 'vendor/model\nAuthorization:NEVER-ECHO'):
            code, out, err = invoke('provider', '--root', str(ROOT), '--provider', 'openrouter',
                                    '--agent', 'aider', '--model', model, '--dry-run')
            self.assertNotEqual(code, 0)
            self.assertNotIn('NEVER-ECHO', out + err)
            self.assertNotIn('Traceback', err)
