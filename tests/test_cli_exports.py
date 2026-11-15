import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from pathlib import Path, PurePosixPath
from shutil import copyfile
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
    def test_lmstudio_generator_preserves_exact_models_and_provider_reasoning_fields(self):
        base = ('model', '--root', str(ROOT), '--agent', 'lm-studio', '--surface', 'desktop',
                '--lmstudio-generator', '--dry-run')
        with tempfile.TemporaryDirectory() as directory, patch('socket.socket', side_effect=AssertionError('unexpected networking')):
            snapshot = Path(directory) / 'lm-studio.json'
            snapshot.write_text(json.dumps({'models': [{'key': 'fixture/local', 'type': 'llm', 'capabilities': {
                'reasoning': {'allowed_options': ['off', 'on'], 'default': 'on'},
            }}]}), encoding='utf-8')
            router = Path(directory) / 'openrouter.json'
            router.write_text(json.dumps({'data': [{'id': 'vendor/arbitrary-model',
                'supported_parameters': ['reasoning'], 'reasoning': {'supported_efforts': ['high']}}]}), encoding='utf-8')
            for provider, model, flags, endpoint, reasoning in (
                ('openai', 'gpt-6-sol', ('--reasoning-effort', 'high'),
                 'https://api.openai.com/v1/chat/completions', {'reasoning_effort': 'high'}),
                ('openrouter', 'vendor/arbitrary-model', ('--catalog', str(router), '--reasoning-effort', 'high'),
                 'https://openrouter.ai/api/v1/chat/completions', {'reasoning': {'effort': 'high'}}),
                ('lm-studio', 'fixture/local', ('--catalog', str(snapshot), '--reasoning', 'off'),
                 'http://localhost:1234/v1/chat/completions', {'reasoning_effort': 'none'}),
            ):
                with self.subTest(provider=provider):
                    code, output, error = invoke(*base, '--provider', provider, '--model', model, *flags)
                    self.assertEqual((code, error), (0, ''))
                    rows = {row['kind']: row for row in json.loads(output)['artifacts']}
                    manifest = json.loads(rows['plugin-manifest']['content'])
                    self.assertEqual(manifest['runner'], 'node')
                    self.assertEqual(manifest['owner'], 'weaver-local')
                    self.assertTrue(manifest['name'].startswith(provider + '-'))
                    package = json.loads(rows['plugin-dependencies']['content'])
                    self.assertEqual(package['dependencies'], {'@lmstudio/sdk': '2.1.0'})
                    self.assertEqual(package['type'], 'module')
                    self.assertEqual(Path(rows['handoff']['path']).parent, Path(rows['plugin-manifest']['path']).parent)
                    source = rows['plugin-source']['content']
                    self.assertIn(f'const endpoint = "{endpoint}";', source)
                    self.assertEqual(json.loads(source.split('const selection = ', 1)[1].split(';', 1)[0]),
                                     {'model': model, **reasoning})
                    self.assertIn('isProtected: true', source)
                    self.assertIn('redirect: "error"', source)
                    self.assertNotIn(snapshot.name, output)
                    code, _, error = invoke(*base[:-1], '--output-dir', directory, '--provider', provider,
                                            '--model', model, *flags)
                    self.assertEqual((code, error), (0, ''))
            self.assertEqual(len(list((Path(directory) / 'lm-studio/generator').glob('*/MODEL-OS-HANDOFF.md'))), 3)
            for extra, error in (
                (('--protocol', 'openai-responses'), 'Chat Completions'),
                (('--reasoning-mode', 'pro'), 'Chat Completions'),
                (('--provider', 'google'), 'Chat Completions'),
                (('--surface', 'bionic'), 'classic Desktop'),
            ):
                with self.subTest(extra=extra):
                    code, output, diagnostic = invoke(*base, '--provider', 'openai', '--model', 'gpt-6-sol', *extra)
                    self.assertNotEqual(code, 0)
                    self.assertEqual(output, '')
                    self.assertIn(error, diagnostic)

    def test_orchestrator_skill_tool_discloses_only_the_index_in_the_request(self):
        base = ('model', '--root', str(ROOT), '--agent', 'perplexity', '--surface', 'orchestrator',
                '--provider', 'perplexity-orchestrator', '--model', 'perplexity/kimi-k3', '--dry-run')
        names = ['security-review', 'token-efficient-retrieval']
        instructions = {
            name: (ROOT / '.agents/skills' / name / 'SKILL.md').read_text(encoding='utf-8')
            .partition('\n---\n')[2].removeprefix('\n') for name in names
        }
        with tempfile.TemporaryDirectory() as directory, patch('socket.socket', side_effect=AssertionError('unexpected networking')):
            path = Path(directory) / 'tools.json'
            path.write_text(json.dumps({'tools': [{'name': 'docs.find', 'inputSchema': {'type': 'object'}}]}),
                            encoding='utf-8')
            mcp = ('--profile', 'efficient', '--orchestrator-mcp-list', f'context7={path}',
                   '--orchestrator-mcp-tool', 'context7:docs.find')
            for protocol, reply_id, flags in (
                ('openai-chat', 'tool_call_id', ()),
                ('openai-responses', 'function_call_output', mcp),
                ('anthropic-messages', 'tool_use_id', ('--reasoning', 'off')),
            ):
                with self.subTest(protocol=protocol):
                    code, output, error = invoke(*base, '--protocol', protocol, '--skill', names[1], '--skill', names[0], *flags)
                    self.assertEqual((code, error), (0, ''))
                    artifacts = {row['kind']: row for row in json.loads(output)['artifacts']}
                    payload = json.loads(artifacts['model-selection']['content'])
                    sidecar = artifacts['skill-tool-map']
                    self.assertEqual(sidecar['path'], 'perplexity/model/skill-tool-map.json')
                    mapping = json.loads(sidecar['content'])
                    self.assertEqual(mapping, {'function': 'weaver_load_skill', 'instructions': instructions})
                    self.assertNotIn('skills', payload)
                    if protocol == 'openai-responses':
                        self.assertEqual(len(payload['tools']), 2)
                        self.assertEqual(next(iter(json.loads(artifacts['mcp-tool-map']['content'])['functions'])),
                                         payload['tools'][0]['name'])
                    else:
                        self.assertEqual(len(payload['tools']), 1)
                        self.assertNotIn('mcp-tool-map', artifacts)
                    declaration = payload['tools'][-1]
                    if protocol == 'openai-chat':
                        self.assertEqual(declaration['type'], 'function')
                        declaration = declaration['function']
                    self.assertEqual(declaration['name'], mapping['function'])
                    self.assertEqual(declaration.get('parameters', declaration.get('input_schema')), {
                        'type': 'object', 'properties': {'name': {'type': 'string', 'enum': names}},
                        'required': ['name'], 'additionalProperties': False,
                    })
                    for name, body in instructions.items():
                        self.assertIn(name, declaration['description'])
                        self.assertNotIn(body.strip(), declaration['description'])
                    self.assertNotIn('benchmark-design', artifacts['model-selection']['content'])
                    self.assertIn(reply_id, artifacts['model-setup']['content'])
                    if protocol == 'anthropic-messages':
                        self.assertEqual(payload['thinking'], {'type': 'disabled'})
            for flags, reason in (
                (('--skill', names[0], '--skill', names[0]), 'Duplicate skill selection'),
                (('--skill', 'missing-skill'), 'Unknown or invalid selected skill'),
                (('--skill', '../security-review'), 'Invalid skill selection'),
                (('--skill', names[0], '--enable', 'context7'), 'Agent API request options'),
            ):
                with self.subTest(flags=flags):
                    code, output, error = invoke(*base, *flags)
                    self.assertNotEqual(code, 0)
                    self.assertEqual(output, '')
                    self.assertIn(reason, error)

    def test_orchestrator_stages_selected_mcp_tools_for_each_protocol(self):
        schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        name = "docs." + "a" * 120
        tool = {"name": name, "description": "Find public docs.", "inputSchema": schema, "annotations": {"readOnlyHint": True}}
        with tempfile.TemporaryDirectory() as directory, patch('socket.socket', side_effect=AssertionError('unexpected networking')):
            root = Path(directory)
            (root / 'catalogs').mkdir()
            for file in ('integrations', 'operating-systems', 'requested-coverage', 'mcp-servers', 'model-providers'):
                copyfile(ROOT / 'catalogs' / f'{file}.json', root / 'catalogs' / f'{file}.json')
            (root / 'profiles').mkdir()
            profile = json.loads((ROOT / 'profiles/efficient.json').read_bytes())
            profile.update(max_mcp_servers=2, allowed_mcp=['context7', 'perplexity'])
            (root / 'profiles/efficient.json').write_text(json.dumps(profile), encoding='utf-8')
            first, second = root / 'tools=list.json', root / 'second.json'
            first.write_text(json.dumps({'tools': [tool, {'name': 'omit_me', 'inputSchema': schema}]}), encoding='utf-8')
            second.write_text(json.dumps({"jsonrpc": "2.0", "id": 1,
                                          "result": {"tools": [{"name": name, "inputSchema": schema}]}}), encoding='utf-8')
            base = ('model', '--root', str(root), '--agent', 'perplexity', '--surface', 'orchestrator',
                    '--provider', 'perplexity-orchestrator', '--model', 'perplexity/kimi-k3', '--dry-run')
            options = ('--profile', 'efficient', '--orchestrator-mcp-list', f'context7={first}',
                       '--orchestrator-mcp-list', f'perplexity={second}', '--orchestrator-mcp-tool', f'context7:{name}',
                       '--orchestrator-mcp-tool', f'perplexity:{name}')
            code, output, error = invoke(*base)
            self.assertEqual((code, error), (0, ''))
            plain = {row['kind']: row['content'] for row in json.loads(output)['artifacts']}
            self.assertNotIn('tools', json.loads(plain['model-selection']))
            self.assertNotIn('mcp-tool-map', plain)
            for args, expected_protocol, reply_id in (
                ((), 'openai-chat', 'tool_call_id'),
                (('--protocol', 'openai-responses'), 'openai-responses', 'call_id'),
                (('--protocol', 'anthropic-messages', '--reasoning', 'off'), 'anthropic-messages', 'tool_use_id'),
            ):
                with self.subTest(protocol=expected_protocol):
                    code, output, error = invoke(*base, *options, *args)
                    self.assertEqual((code, error), (0, ''))
                    artifacts = {row['kind']: row['content'] for row in json.loads(output)['artifacts']}
                    body = json.loads(artifacts['model-selection'])
                    mapping = json.loads(artifacts['mcp-tool-map'])
                    self.assertEqual(body['model'], 'perplexity/kimi-k3')
                    self.assertEqual(mapping['protocol'], expected_protocol)
                    self.assertEqual(mapping['snapshot_sha256'], {
                        'context7': sha256(first.read_bytes()).hexdigest(),
                        'perplexity': sha256(second.read_bytes()).hexdigest(),
                    })
                    functions = body['tools']
                    self.assertEqual(len(functions), 2)
                    for emitted, server, description in zip(
                        functions, ('context7', 'perplexity'),
                        ('Find public docs.', f'Call the selected MCP tool {name}.'),
                        strict=True,
                    ):
                        definition = emitted.get('function', emitted)
                        alias = definition['name']
                        self.assertRegex(alias, r"\A[A-Za-z0-9_-]{1,64}\Z")
                        self.assertEqual(mapping['functions'][alias], {'server': server, 'tool': name})
                        fields = {'name': alias, 'description': description}
                        function = {**fields, 'parameters': schema, 'strict': False}
                        expected = {
                            'openai-chat': {'type': 'function', 'function': function},
                            'openai-responses': {'type': 'function', **function},
                            'anthropic-messages': {**fields, 'input_schema': schema},
                        }[expected_protocol]
                        self.assertEqual(emitted, expected)
                    if expected_protocol == 'anthropic-messages':
                        self.assertEqual(body['thinking'], {'type': 'disabled'})
                    self.assertIn(reply_id, artifacts['model-setup'])
                    self.assertNotIn('omit_me', output)
                    self.assertNotIn('https://api.perplexity.ai/mcp', output)
                    self.assertNotIn(first.name, output)

    def test_orchestrator_mcp_rejects_unapproved_or_invalid_selections(self):
        base = ('model', '--root', str(ROOT), '--agent', 'perplexity', '--surface', 'orchestrator',
                '--provider', 'perplexity-orchestrator', '--model', 'perplexity/kimi-k3', '--dry-run')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tools.json'
            options = ('--profile', 'efficient', '--orchestrator-mcp-list', f'context7={path}',
                       '--orchestrator-mcp-tool', 'context7:chosen')
            valid = {'name': 'chosen', 'inputSchema': {'type': 'object'}}
            cases = (
                ({'tools': [valid]}, options + ('--orchestrator-mcp-tool', 'context7:chosen'), 'Duplicate'),
                ({'tools': [valid]}, options[:4], 'together'),
                ({'tools': [valid]}, options[:-1] + ('context7:missing',), 'missing from the saved list'),
                ({'tools': [valid]}, options[:3] + (f'perplexity={path}', options[4], 'perplexity:chosen'), 'not permitted'),
                ({'tools': [valid]}, options + ('--enable', 'context7'), 'Agent API request options'),
                ({'tools': [valid, valid]}, options, 'duplicate name'),
                ({'tools': [{**valid, 'inputSchema': {'type': 'string'}}]}, options, 'object inputSchema'),
                ({'tools': [{**valid, 'description': 'Use Bearer example-credential'}]}, options, 'Secret-like'),
                ({'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32603}, 'tools': [valid]}, options, 'successful MCP'),
            )
            for saved, flags, expected in cases:
                with self.subTest(expected=expected):
                    path.write_text(json.dumps(saved), encoding='utf-8')
                    code, output, error = invoke(*base, *flags)
                    self.assertNotEqual(code, 0)
                    self.assertEqual(output, '')
                    self.assertIn(expected, error)
                    self.assertNotIn('example-credential', error)
            wrong_surface = ('model', '--root', str(ROOT), '--agent', 'perplexity', '--surface', 'agent-api',
                             '--provider', 'perplexity-agent', '--model', 'perplexity/kimi-k3', '--dry-run')
            self.assertIn('require the Perplexity Orchestrator route', invoke(*wrong_surface, *options)[2])

    def test_perplexity_inline_skills_compose_with_model_and_mcp_request_fragments(self):
        base = ('--root', str(ROOT), '--agent', 'perplexity', '--surface', 'agent-api', '--dry-run')
        name = 'token-efficient-retrieval'
        bundle = ('bundle', *base, '--profile', 'efficient', '--perplexity-inline-skills')
        with patch('socket.socket', side_effect=AssertionError('unexpected networking')):
            code, output, error = invoke(*bundle, '--skill', name)
            self.assertEqual((code, error), (0, ''))
            artifacts = json.loads(output)['artifacts']
            self.assertFalse(any(row['kind'] == 'skill-import' for row in artifacts))
            skill, = [row for row in artifacts if row['kind'] == 'skill-request']
            self.assertEqual(skill['path'], 'perplexity/requests/agent-api.skills.json')
            self.assertIn('enables Perplexity\'s request sandbox', artifacts[0]['content'])
            code, output, error = invoke('model', *base, '--provider', 'perplexity-agent',
                                         '--model', 'anthropic/claude-opus-5-5',
                                         '--perplexity-fallback-model', 'openai/gpt-6.1-sol',
                                         '--reasoning-effort', 'high', '--perplexity-max-steps', '4')
            self.assertEqual((code, error), (0, ''))
            model, = [row for row in json.loads(output)['artifacts'] if row['kind'] == 'model-selection']
            code, output, error = invoke('render', *base, '--profile', 'efficient', '--enable', 'context7',
                                         '--perplexity-allow-tool', 'context7:query-docs',
                                         '--perplexity-defer-tools', 'context7')
            self.assertEqual((code, error), (0, ''))
            mcp, = [row for row in json.loads(output)['artifacts'] if row['kind'] == 'mcp-request']
        request = {**json.loads(skill['content']), **json.loads(model['content']), **json.loads(mcp['content'])}
        self.assertEqual(set(request), {'skills', 'models', 'reasoning', 'tools', 'max_steps'})
        self.assertEqual(request['skills'][0]['name'], name)
        self.assertEqual(request['reasoning'], {'effort': 'high'})
        self.assertEqual(request['max_steps'], 4)
        self.assertEqual(request['tools'][0]['allowed_tools'], ['query-docs'])
        self.assertIs(request['tools'][0]['defer_loading'], True)
        self.assertEqual(request['tools'][0]['require_approval'], 'always')
        combined = ('model', *base, '--provider', 'perplexity-agent', '--model', 'anthropic/claude-opus-5-5',
                    '--perplexity-fallback-model', 'openai/gpt-6.1-sol', '--reasoning-effort', 'high',
                    '--perplexity-max-steps', '4', '--profile', 'efficient', '--skill', name,
                    '--enable', 'context7', '--perplexity-allow-tool', 'context7:query-docs',
                    '--perplexity-defer-tools', 'context7')
        for protocol, endpoint in (('perplexity-agent', '/v1/agent'), ('openai-responses', '/v1/responses')):
            with self.subTest(protocol=protocol), patch('socket.socket', side_effect=AssertionError('unexpected networking')):
                code, output, error = invoke(*combined, '--protocol', protocol)
                self.assertEqual((code, error), (0, ''))
                artifacts = json.loads(output)['artifacts']
                fields, = [row for row in artifacts if row['kind'] == 'model-selection']
                self.assertEqual(fields['path'], 'perplexity/model/model-selection.json')
                self.assertEqual(json.loads(fields['content']), request)
                handoff, = [row['content'] for row in artifacts if row['kind'] == 'model-setup']
                self.assertIn(f'POST {endpoint}', handoff)
                self.assertIn('resend the reviewed tool declarations unchanged', handoff)
        self.assertEqual(invoke(*bundle)[0], 2)
        for surface in ('app', 'orchestrator'):
            arguments = ('bundle', '--root', str(ROOT), '--agent', 'perplexity', '--surface', surface,
                         '--dry-run', '--profile', 'efficient', '--perplexity-inline-skills', '--skill', name)
            self.assertEqual(invoke(*arguments)[0], 2)

    def test_perplexity_combined_request_is_opt_in_and_validates_each_selection(self):
        base = ('model', '--root', str(ROOT), '--agent', 'perplexity', '--dry-run')
        target = ('--surface', 'agent-api', '--provider', 'perplexity-agent')
        model = ('--model', 'openai/gpt-6.1-sol')
        skill = ('--skill', 'token-efficient-retrieval')
        mcp = ('--enable', 'context7')
        profile = ('--profile', 'efficient')
        preset = ('--perplexity-effort-preset', 'high')
        saved = ('--perplexity-profile', 'profile_example', '--perplexity-profile-version', '3')
        for args, expected in ((model, {'model'}), (model + profile + skill, {'model', 'skills'}),
                               (preset + profile + mcp, {'effort', 'tools'}),
                               (saved + profile + skill + mcp, {'profile', 'skills', 'tools'})):
            with self.subTest(args=args):
                code, output, error = invoke(*base, *target, *args)
                self.assertEqual((code, error), (0, ''))
                row, = [row for row in json.loads(output)['artifacts'] if row['kind'] == 'model-selection']
                fields = json.loads(row['content'])
                self.assertEqual(set(fields), expected)
                if 'tools' in fields:
                    self.assertEqual(fields['tools'][0]['require_approval'], 'always')
        for invalid in (profile, skill, mcp, profile + ('--skill', '../private'),
                        profile + skill + skill, profile + skill + ('--perplexity-defer-tools', 'context7'),
                        profile + ('--enable', 'github'), profile + mcp + ('--perplexity-allow-tool', 'context7:*'),
                        ('--profile', 'enterprise-secure') + mcp):
            with self.subTest(invalid=invalid):
                code, output, _ = invoke(*base, *target, *model, *invalid)
                self.assertEqual((code, output), (2, ''))
        self.assertEqual(invoke(*base, '--surface', 'orchestrator', '--provider', 'perplexity-orchestrator',
                                *model, *profile, *skill, *mcp)[0], 2)

    def test_perplexity_mcp_controls_require_selected_servers_and_exact_names(self):
        base = ('render', '--root', str(ROOT), '--agent', 'perplexity', '--dry-run', '--profile', 'efficient')
        target = (*base, '--surface', 'agent-api', '--enable', 'context7')
        for options in (
            ('--perplexity-allow-tool', 'context7:*'), ('--perplexity-allow-tool', 'other:query-docs'),
            ('--perplexity-allow-tool', 'context7:query-docs', '--perplexity-allow-tool', 'context7:query-docs'),
            ('--perplexity-defer-tools', 'other'),
        ):
            with self.subTest(options=options):
                self.assertEqual(invoke(*target, *options)[0], 2)
        self.assertEqual(invoke(*base, '--surface', 'app', '--enable', 'context7',
                                '--perplexity-defer-tools', 'context7')[0], 2)

    def test_perplexity_agent_fallback_uses_a_saved_model_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / 'perplexity-models.json'
            catalog.write_text(json.dumps({'data': [{'id': 'openai/fixture'}, {'id': 'anthropic/fixture'}]}), encoding='utf-8')
            base = ('model', '--root', str(ROOT), '--agent', 'perplexity', '--dry-run', '--model', 'openai/fixture',
                    '--perplexity-fallback-model', 'anthropic/fixture', '--perplexity-max-output-tokens', '4096')
            target = ('--surface', 'agent-api', '--provider', 'perplexity-agent')
            code, output, error = invoke(*base, *target, '--catalog', str(catalog))
            self.assertEqual((code, error), (0, ''))
            fields, = [row for row in json.loads(output)['artifacts'] if row['kind'] == 'model-selection']
            self.assertEqual(json.loads(fields['content']), {
                'models': ['openai/fixture', 'anthropic/fixture'], 'max_output_tokens': 4096,
            })
            self.assertEqual(invoke(*base, *target)[0], 2)
            self.assertEqual(invoke(*base, '--surface', 'orchestrator', '--provider', 'perplexity-orchestrator',
                                    '--catalog', str(catalog))[0], 2)

    def test_perplexity_service_tier_cli_stages_fast_as_priority(self):
        base = ('model', '--root', str(ROOT), '--agent', 'perplexity', '--dry-run',
                '--model', 'openai/gpt-6.1-sol', '--perplexity-service-tier', 'fast')
        code, output, error = invoke(*base, '--surface', 'agent-api', '--provider', 'perplexity-agent')
        self.assertEqual((code, error), (0, ''))
        fields, = [row for row in json.loads(output)['artifacts'] if row['kind'] == 'model-selection']
        self.assertEqual(json.loads(fields['content']), {'model': 'openai/gpt-6.1-sol', 'service_tier': 'priority'})
        code, _, error = invoke(*base, '--surface', 'orchestrator', '--provider', 'perplexity-orchestrator')
        self.assertEqual(code, 2)
        self.assertIn('require the Perplexity Agent API route', error)

    def test_perplexity_agent_api_stages_effort_or_profile_without_a_model(self):
        base = ('model', '--root', str(ROOT), '--agent', 'perplexity', '--dry-run',
                '--surface', 'agent-api', '--provider', 'perplexity-agent')
        for args, expected in (
            *((('--perplexity-effort-preset', preset), {'effort': preset})
              for preset in ('fast', 'low', 'medium', 'high', 'xhigh', 'ultra')),
            (('--perplexity-effort-preset', 'ultra@2026-11-11.0', '--perplexity-service-tier', 'flex'),
             {'effort': 'ultra@2026-11-11.0', 'service_tier': 'flex'}),
            (('--perplexity-effort-preset', 'fast@latest', '--perplexity-max-output-tokens', '512'),
             {'effort': 'fast@latest', 'max_output_tokens': 512}),
            (('--perplexity-effort-preset', 'fast', '--perplexity-max-steps', '1'), {'effort': 'fast', 'max_steps': 1}),
            (('--perplexity-profile', 'profile_example', '--perplexity-max-steps', '500'),
             {'profile': {'type': 'custom', 'id': 'profile_example'}, 'max_steps': 500}),
            (('--perplexity-effort-preset', 'high@release_7'), {'effort': 'high@release_7'}),
            (('--perplexity-profile', 'profile_example'), {'profile': {'type': 'custom', 'id': 'profile_example'}}),
            (('--perplexity-profile', 'profile_example', '--perplexity-profile-version', '3',
              '--model', 'perplexity/kimi-k3', '--reasoning-effort', 'max'),
             {'profile': {'type': 'custom', 'id': 'profile_example', 'version': '3'},
              'model': 'perplexity/kimi-k3', 'reasoning': {'effort': 'max'}}),
        ):
            with self.subTest(args=args):
                code, output, error = invoke(*base, *args)
                self.assertEqual((code, error), (0, ''))
                fields, = [row for row in json.loads(output)['artifacts'] if row['kind'] == 'model-selection']
                self.assertEqual(json.loads(fields['content']), expected)
        for args in (
            (), ('--perplexity-profile-version', 'latest'), ('--perplexity-effort-preset', 'max'),
            ('--perplexity-effort-preset', 'ultra@'),
            ('--perplexity-effort-preset', 'ultra@revision@next'),
            ('--perplexity-effort-preset', 'ultra@two words'),
            ('--perplexity-effort-preset', 'high', '--perplexity-profile', 'profile_example'),
            ('--perplexity-effort-preset', 'high', '--reasoning-effort', 'high'),
            ('--perplexity-effort-preset', 'fast', '--perplexity-max-steps', '0'),
            ('--perplexity-profile', 'profile_example', '--perplexity-max-steps', '501'),
            ('--perplexity-profile', 'profile_example', '--perplexity-fallback-model', 'perplexity/kimi-k3'),
            ('--perplexity-profile', '../secret'), ('--perplexity-profile', 'profile_example',
                                                   '--perplexity-profile-version', '../secret'),
        ):
            with self.subTest(args=args):
                self.assertEqual(invoke(*base, *args)[0], 2)
        self.assertEqual(invoke('model', '--root', str(ROOT), '--dry-run', '--agent', 'qoder',
                                '--surface', 'cli')[0], 2)

    def test_kilo_model_stages_variant_as_a_single_argument_and_requires_text_output(self):
        row = {'providerID': 'fixture', 'id': 'vendor/review', 'enabled': True, 'type': 'chat',
               'variants': [{'id': 'deep'}, {'id': '-extra'}], 'capabilities': {'output': ['text']}}
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / 'selectors.json'
            catalog.write_text(json.dumps({'location': {}, 'data': [row]}), encoding='utf-8')
            base = ('model', '--root', str(ROOT), '--dry-run', '--model', 'fixture/vendor/review')
            native = (*base, '--agent', 'kilo', '--surface', 'cli', '--kilo-catalog', str(catalog))
            desktop = (*base, '--agent', 'kilo', '--surface', 'desktop', '--kilo-catalog', str(catalog))
            for variant in (None, 'deep', '-extra'):
                with self.subTest(variant=variant):
                    options = () if variant is None else (f'--model-variant={variant}',)
                    code, output, error = invoke(*native, *options)
                    self.assertEqual((code, error), (0, ''))
                    files = {item['path']: item for item in json.loads(output)['artifacts']}
                    argv = json.loads(files['kilo/model/session-arguments.json']['content'])
                    expected = ['run', '--model', 'fixture/vendor/review']
                    if variant is not None:
                        expected.append(f'--variant={variant}')
                    self.assertEqual(argv, expected)
                    code, output, error = invoke(*desktop, *options)
                    self.assertEqual((code, error), (0, ''))
                    files = {item['path']: item for item in json.loads(output)['artifacts']}
                    self.assertNotIn('kilo/model/session-arguments.json', files)
                    selection = json.loads(files['kilo/model/desktop-selection.json']['content'])
                    expected_selection = {'model': 'fixture/vendor/review'}
                    if variant is not None:
                        expected_selection['variant'] = variant
                    self.assertEqual(selection, expected_selection)
                    self.assertIn('chat model picker', files['kilo/model/model-setup.md']['content'])
            for invalid in (('--model-variant', 'high'), ('--reasoning-effort', 'high')):
                self.assertEqual(invoke(*native, *invalid)[0], 2)
                self.assertEqual(invoke(*desktop, *invalid)[0], 2)
            self.assertEqual(invoke(*base, '--agent', 'kilo', '--surface', 'ide', '--kilo-catalog', str(catalog))[0], 2)
            catalog.write_text(json.dumps({'location': {}, 'data': [row | {'capabilities': {'output': ['image']}}]}),
                               encoding='utf-8')
            code, _, error = invoke(*native)
            self.assertEqual(code, 2)
            self.assertIn('must advertise text output', error)
            self.assertEqual(invoke(*desktop)[0], 2)

    def test_opencode_model_stages_a_catalog_alias_and_named_variant(self):
        row = {'providerID': 'fixture', 'id': 'alias/review', 'modelID': 'wire-review',
               'enabled': True, 'type': 'chat', 'variants': [{'id': 'review-deep'}]}
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / 'selectors.json'
            catalog.write_text(json.dumps({'location': {}, 'data': [row]}), encoding='utf-8')
            base = ('model', '--root', str(ROOT), '--dry-run', '--model', 'fixture/alias/review')
            native = (*base, '--agent', 'opencode', '--surface', 'cli')
            for options, selector in (((), 'fixture/alias/review'),
                                      (('--model-variant', 'review-deep'), 'fixture/alias/review#review-deep')):
                with self.subTest(selector=selector):
                    code, output, error = invoke(*native, '--opencode-catalog', str(catalog), *options)
                    self.assertEqual((code, error), (0, ''))
                    files = {item['path']: item for item in json.loads(output)['artifacts']}
                    content = files['opencode/model/session-arguments.json']['content']
                    self.assertEqual(json.loads(content), ['run', '--standalone', '--model', selector])
                    self.assertNotIn('wire-review', content)
            for invalid in (('--model-variant', 'high'), ('--reasoning-effort', 'high'), ('--provider', 'openrouter')):
                with self.subTest(invalid=invalid):
                    self.assertEqual(invoke(*native, '--opencode-catalog', str(catalog), *invalid)[0], 2)
            self.assertEqual(invoke(*native, '--model-variant', 'review-deep')[0], 2)
            self.assertEqual(invoke(*base, '--agent', 'kilo', '--opencode-catalog', str(catalog))[0], 2)
            catalog.write_text(json.dumps({'location': {}, 'data': [row | {'enabled': False}]}), encoding='utf-8')
            self.assertEqual(invoke(*native, '--opencode-catalog', str(catalog))[0], 2)

    def test_lmstudio_responses_mcp_handoff_points_to_the_selected_endpoint(self):
        common = ('--root', str(ROOT), '--profile', 'efficient', '--dry-run')
        args = ('--enable', 'context7', '--lmstudio-responses', '--lmstudio-allow-tool', 'context7:resolve-library-id')
        code, output, error = invoke('render', '--agent', 'lm-studio', '--surface', 'api', *common, *args)
        self.assertEqual(code, 0, error)
        artifacts = {row['path']: row for row in json.loads(output)['artifacts']}
        fragment = json.loads(artifacts['lm-studio/requests/responses.json']['content'])
        self.assertEqual(fragment['tools'][0]['allowed_tools'], ['resolve-library-id'])
        self.assertIn('POST /v1/responses', artifacts['lm-studio/MCP-HANDOFF.md']['content'])
        self.assertEqual(invoke('render', '--agent', 'perplexity', '--surface', 'agent-api', *common, *args)[0], 2)

    def test_lmstudio_plugin_handoff_identifies_authentication_and_native_chat(self):
        args = ('render', '--agent', 'lm-studio', '--surface', 'api', '--root', str(ROOT), '--profile', 'efficient',
                '--enable', 'context7', '--lmstudio-mcp-plugins', '--dry-run')
        code, output, error = invoke(*args)
        self.assertEqual(code, 0, error)
        artifacts = {row['path']: row for row in json.loads(output)['artifacts']}
        fragment = json.loads(artifacts['lm-studio/requests/chat-plugins.json']['content'])
        self.assertEqual(fragment, {'integrations': [{'type': 'plugin', 'id': 'mcp/context7'}]})
        handoff = artifacts['lm-studio/MCP-HANDOFF.md']['content']
        self.assertIn('POST /api/v1/chat', handoff)
        self.assertIn('require authentication', handoff)
        self.assertEqual(invoke(*args, '--lmstudio-responses')[0], 2)

    def test_devin_mcp_controls_apply_only_to_exact_tools_on_selected_servers(self):
        common = ('--root', str(ROOT), '--profile', 'efficient', '--dry-run')
        devin = ('render', '--agent', 'devin', '--enable', 'context7', *common)
        code, output, error = invoke(*devin, '--devin-disable-tool', 'context7:query-docs')
        self.assertEqual(code, 0, error)
        artifact = next(row for row in json.loads(output)['artifacts'] if row['kind'] == 'mcp')
        entry = json.loads(artifact['content'])['mcpServers']['context7']
        self.assertEqual(entry['disabledTools'], ['query-docs'])
        self.assertNotIn('disabled', entry)
        for options in (('other:query-docs',), ('context7:*',), ('context7:query-docs', 'context7:query-docs')):
            with self.subTest(options=options):
                self.assertEqual(invoke(*devin, *[part for tool in options for part in ('--devin-disable-tool', tool)])[0], 2)
        self.assertEqual(invoke('render', '--agent', 'devin', *common, '--devin-stage-disabled')[0], 2)
        self.assertEqual(invoke('render', '--agent', 'codex', '--enable', 'context7', *common,
                                '--devin-disable-tool', 'context7:query-docs')[0], 2)

    def test_kiro_mcp_filters_require_exact_names_and_explicit_selection(self):
        common = ('render', '--root', str(ROOT), '--agent', 'kiro', '--profile', 'efficient', '--dry-run')
        selected = (*common, '--surface', 'cli', '--enable', 'context7')
        code, output, error = invoke(*selected, '--kiro-disable-tool', 'context7:query-docs')
        self.assertEqual(code, 0, error)
        fragment, handoff = json.loads(output)['artifacts']
        self.assertEqual(fragment['path'], 'kiro/.kiro/settings/mcp.json')
        self.assertEqual(json.loads(fragment['content'])['mcpServers']['context7'], {
            'url': 'https://mcp.context7.com/mcp', 'disabled': True, 'disabledTools': ['query-docs'],
        })
        self.assertIn('Review any disabledTools filters', handoff['content'])
        for tools in (('context7',), ('context7:*',), ('other:query-docs',),
                      ('context7:query-docs', 'context7:query-docs')):
            with self.subTest(tools=tools):
                options = [part for tool in tools for part in ('--kiro-disable-tool', tool)]
                self.assertEqual(invoke(*selected, *options)[0], 2)
        self.assertEqual(invoke(*common, '--surface', 'cli', '--kiro-disable-tool', 'context7:query-docs')[0], 2)
        code, output, error = invoke(*common, '--surface', 'ide', '--enable', 'context7',
                                     '--kiro-disable-tool', 'context7:query-docs')
        self.assertEqual(code, 0, error)
        fragment, handoff = json.loads(output)['artifacts']
        self.assertEqual(json.loads(fragment['content'])['mcpServers']['context7'], {
            'url': 'https://mcp.context7.com/mcp', 'disabledTools': ['query-docs'],
        })
        self.assertIn('Review any disabledTools filters', handoff['content'])

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
