"""End-to-end offline CLI coverage for explicit product/model/agent selections."""

import json
import os
import tempfile
import tomllib
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import yaml
from test_cli_exports import invoke

ROOT = Path(__file__).resolve().parents[1]
ROOT_ARGS = ('--root', str(ROOT))


class CapabilityCliTests(unittest.TestCase):
    def test_requested_scope_does_not_turn_mapping_into_full_support(self):
        with mock.patch.object(urllib.request, 'urlopen', side_effect=AssertionError('network')):
            code, output, error = invoke('coverage', *ROOT_ARGS, '--format', 'json')
        self.assertEqual(code, 0, error)
        data = json.loads(output)
        self.assertEqual(data['summary']['requested_items'], 23)
        self.assertEqual(data['summary']['mapped_items'], 23)
        self.assertGreater(data['summary']['pending_runtime_capabilities'], 0)
        self.assertFalse(data['full_requested_support_verified'])
        self.assertIn('model_catalogs', data)
        self.assertIn('custom_agents', data)
        self.assertFalse(data['mcp_transports']['stdio']['runtime_verified'])
        self.assertEqual(data['mcp_transports']['stdio']['unestablished_file_exports'],
                         ['jetbrains-air/desktop', 'jetbrains-air/ide'])
        self.assertIn('codex', data['mcp_transports']['stdio']['documented_adapters'])
        self.assertEqual(invoke('coverage', *ROOT_ARGS, '--strict')[0], 1)

    def test_host_model_controls_filter_alias_and_surface_without_implying_availability(self):
        code, output, error = invoke('models', *ROOT_ARGS, '--agent', 'OpenAI Codex', '--surface', 'ide')
        self.assertEqual(code, 0, error)
        data = json.loads(output)
        self.assertEqual([(entry['product_id'], entry['surface_id']) for entry in data['hosts']],
                         [('codex', 'ide')])
        self.assertEqual(data['hosts'][0]['availability'], 'unknown')
        self.assertFalse(data['hosts'][0]['runtime_checks'])
        self.assertEqual(invoke('models', *ROOT_ARGS, '--surface', 'ide')[0], 2)
        self.assertEqual(invoke('models', *ROOT_ARGS, '--agent', 'codex', '--surface', 'missing')[0], 2)
        self.assertEqual(invoke('models', *ROOT_ARGS, '--agent', 'VSCodeagents')[0], 2)

    def test_model_snapshot_import_rejects_host_filter_instead_of_silently_ignoring_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic-models.json'
            path.write_text(json.dumps({'data': [{'id': 'synthetic-model'}]}), encoding='utf-8')
            code, output, error = invoke('models', *ROOT_ARGS, '--provider', 'openai', '--input', str(path),
                                         '--agent', 'codex')
        self.assertEqual(code, 2)
        self.assertEqual(output, '')
        self.assertIn('separately', error)

    def test_exact_aliases_render_canonical_paths_and_groups_fail_closed(self):
        code, output, error = invoke('render', *ROOT_ARGS, '--agent', 'Factory Droid',
                                     '--profile', 'balanced', '--dry-run')
        self.assertEqual(code, 0, error)
        paths = [item['path'] for item in json.loads(output)['artifacts']]
        self.assertIn('factory/.factory/mcp.json', paths)
        self.assertTrue(all(path.startswith('factory/') for path in paths))
        code, output, error = invoke('render', *ROOT_ARGS, '--agent', 'VSCodeagents',
                                     '--profile', 'balanced', '--dry-run')
        self.assertEqual(code, 2)
        self.assertEqual(output, '')
        self.assertIn('multiple products', error)

    def test_qoder_ide_cli_stages_manual_mcp_import_and_native_skills_separately(self):
        code, output, error = invoke('render', *ROOT_ARGS, '--agent', 'qoder', '--surface', 'ide',
                                     '--profile', 'balanced', '--dry-run')
        self.assertEqual(code, 0, error)
        artifacts = json.loads(output)['artifacts']
        fragment = next(item for item in artifacts if item['kind'] == 'mcp-fragment')
        self.assertEqual(fragment['path'], 'qoder/fragments/mcp.ide.json')
        self.assertEqual(json.loads(fragment['content']), {'mcpServers': {}})
        self.assertFalse(any(item['path'].endswith('.qoder/settings.json') for item in artifacts))
        handoff = next(item['content'] for item in artifacts if item['kind'] == 'handoff')
        self.assertIn('Application-scope fragment', handoff)
        self.assertIn('Qoder IDE Settings', handoff)
        self.assertNotIn('Project target', handoff)
        code, output, error = invoke('bundle', *ROOT_ARGS, '--agent', 'qoder', '--surface', 'ide',
                                     '--profile', 'balanced', '--skill', 'token-efficient-retrieval', '--dry-run')
        self.assertEqual(code, 0, error)
        paths = [item['path'] for item in json.loads(output)['artifacts']]
        self.assertIn('qoder/AGENTS.md', paths)
        self.assertIn('qoder/.qoder/skills/token-efficient-retrieval/SKILL.md', paths)

    def test_models_import_all_snapshot_entries_and_keep_unknown_availability(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic-models.json'
            path.write_text(json.dumps({'data': [{'id': f'synthetic-model-{n}'} for n in range(131)]}),
                            encoding='utf-8')
            code, output, error = invoke('models', *ROOT_ARGS, '--provider', 'openai', '--input', str(path))
            self.assertEqual(code, 0, error)
            data = json.loads(output)
            self.assertEqual(data['model_count'], 131)
            self.assertEqual(data['completeness'], 'unknown')
            self.assertFalse(data['inference_tested'])
            self.assertNotIn(str(path), output)
            self.assertEqual(invoke('models', *ROOT_ARGS, '--input', str(path))[0], 2)

    def test_model_reasoning_uses_selected_provider_snapshot_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            snapshot = root / 'synthetic-models.json'
            snapshot.write_text(json.dumps({'data': [{'id': 'fixture/synthetic-model', 'reasoning': {
                'supported_efforts': ['low', 'high'], 'mandatory': False,
                'default_enabled': False, 'supports_max_tokens': True,
            }}]}), encoding='utf-8')
            args = ('model', *ROOT_ARGS, '--agent', 'Qwen Code', '--provider', 'openrouter',
                    '--model', 'fixture/synthetic-model', '--catalog', str(snapshot),
                    '--reasoning-effort', 'high', '--output-dir', str(root / 'staged'), '--dry-run')
            with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'NEVER-ECHO'}), \
                 mock.patch.object(urllib.request, 'urlopen', side_effect=AssertionError('network')):
                code, output, error = invoke(*args)
            self.assertEqual(code, 0, error)
            artifacts = json.loads(output)['artifacts']
            selection = next(item for item in artifacts if item['kind'] == 'model-selection')
            self.assertEqual(json.loads(selection['content'])['reasoning'], {'effort': 'high'})
            self.assertNotIn('NEVER-ECHO', output + error)
            self.assertFalse((root / 'staged').exists())

    def test_unknown_reasoning_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory).resolve() / 'not-created'
            code, output, error = invoke('model', *ROOT_ARGS, '--agent', 'codex', '--provider', 'openai',
                                         '--model', 'unknown-future-model', '--reasoning-effort', 'high',
                                         '--output-dir', str(destination))
            self.assertEqual(code, 2)
            self.assertEqual(output, '')
            self.assertNotIn('Traceback', error)
            self.assertFalse(destination.exists())

    def test_zero_provider_context_reaches_manual_handoff_without_becoming_an_explicit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic-nontext-model.json'
            path.write_text(json.dumps({'data': [{'id': 'fixture/nontext-zero-context', 'context_length': 0,
                'architecture': {'input_modalities': ['text'], 'output_modalities': ['image']}}]}), encoding='utf-8')
            args = ('model', *ROOT_ARGS, '--agent', 'qwen-code', '--provider', 'openrouter',
                    '--model', 'fixture/nontext-zero-context', '--catalog', str(path), '--dry-run')
            code, output, error = invoke(*args)
            self.assertEqual(code, 0, error)
            artifacts = json.loads(output)['artifacts']
            self.assertFalse(any(item['kind'] == 'model-config' for item in artifacts))
            self.assertIn('Configuration state: `manual`', next(item['content'] for item in artifacts
                                                              if item['kind'] == 'model-setup'))
            code, output, error = invoke(*args, '--context-size', '0')
            self.assertEqual(code, 2)
            self.assertEqual(output, '')
            self.assertNotIn('Traceback', error)

    def test_provider_command_uses_selected_native_provider(self):
        code, output, error = invoke('provider', *ROOT_ARGS, '--agent', 'codex', '--provider', 'anthropic',
                                     '--model', 'synthetic-model', '--dry-run')
        self.assertEqual(code, 0, error)
        artifacts = json.loads(output)['artifacts']
        handoff = next(item['content'] for item in artifacts if item['kind'] == 'model-setup')
        self.assertIn('Provider: `anthropic`', handoff)
        self.assertIn('https://api.anthropic.com', handoff)
        self.assertNotIn('https://openrouter.ai', handoff)

    def test_native_custom_agent_cli_is_staged_and_preserves_inherited_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            instructions = root / 'synthetic-instructions.txt'
            instructions.write_bytes(b'Review the supplied fixture.\r\nDo not change files.\r\n')
            code, output, error = invoke('agent', *ROOT_ARGS, '--agent', 'OpenAI Codex', '--surface', 'ide',
                                         '--name', 'review-helper', '--description', 'Read supplied code.',
                                         '--instructions-file', str(instructions), '--output-dir', str(root / 'out'),
                                         '--dry-run')
            self.assertEqual(code, 0, error)
            artifacts = json.loads(output)['artifacts']
            native = next(item for item in artifacts if item['kind'] == 'agent-definition')
            config = tomllib.loads(native['content'])
            self.assertEqual(config['name'], 'review-helper')
            self.assertNotIn('model', config)
            self.assertNotIn('model_reasoning_effort', config)
            self.assertNotIn('\r', native['content'])
            self.assertFalse((root / 'out').exists())
        code, output, error = invoke('agents', 'inventory', *ROOT_ARGS, '--agent', 'OpenAI Codex', '--surface', 'ide')
        self.assertEqual(code, 0, error)
        rows = json.loads(output)['definitions']
        self.assertEqual([(row['product_id'], row['surface_id']) for row in rows], [('codex', 'ide')])

    def test_openhands_local_profile_is_explicit_data_without_model_or_catalog_override(self):
        with tempfile.TemporaryDirectory() as directory:
            instructions = Path(directory) / 'synthetic-instructions.txt'
            instructions.write_text('Review only the context supplied by the parent.', encoding='utf-8')
            args = ('agent', *ROOT_ARGS, '--agent', 'openhands', '--name', 'profile-review',
                    '--description', 'Review supplied fixture context.', '--instructions-file', str(instructions),
                    '--llm-profile', 'team.review.json', '--dry-run')
            code, output, error = invoke(*args)
            self.assertEqual(code, 0, error)
            definition = next(item for item in json.loads(output)['artifacts'] if item['kind'] == 'agent-definition')
            self.assertEqual(definition['path'], 'openhands/.agents/agents/profile-review.md')
            config = yaml.safe_load(definition['content'].split('---\n', 2)[1])
            self.assertEqual(config['model'], 'team.review.json')
            self.assertEqual(config['tools'], [])
            code, output, error = invoke(*args, '--provider', 'openrouter',
                                         '--catalog', str(Path(directory) / 'not-read.json'))
            self.assertEqual(code, 2)
            self.assertEqual(output, '')
            self.assertIn('cannot be combined', error)
            self.assertNotIn('not-read', error)

    def test_custom_agent_cli_force_cannot_replace_manual_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            instructions = root / 'synthetic-instructions.txt'
            instructions.write_text('Review only the supplied fixture.', encoding='utf-8')
            destination = root / 'out'
            args = ('agent', *ROOT_ARGS, '--agent', 'codex', '--name', 'review-helper',
                    '--description', 'Inspect fixtures.', '--instructions-file', str(instructions),
                    '--output-dir', str(destination))
            self.assertEqual(invoke(*args)[0], 0)
            self.assertEqual(invoke(*args)[0], 2)
            self.assertEqual(invoke(*args, '--force')[0], 0)
            target = destination / 'codex/.codex/agents/review-helper.toml'
            target.write_bytes(b'user edit\n')
            self.assertEqual(invoke(*args, '--force')[0], 2)
            self.assertEqual(target.read_bytes(), b'user edit\n')


if __name__ == '__main__':
    unittest.main()
