"""Offline checks for Antigravity SDK configuration exports."""
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from tests.test_cli_exports import ROOT, invoke


class AntigravitySdkTests(unittest.TestCase):
    def run_export(self, command, *options):
        with (patch('agentweaver.cli.detect_host_os', return_value='windows'),
              patch('socket.socket', side_effect=AssertionError('unexpected network'))):
            return invoke(command, '--root', str(ROOT), '--agent', 'antigravity', '--surface', 'sdk',
                          '--os', 'windows', '--dry-run', *options)

    def artifacts(self, command, *options):
        code, output, errors = self.run_export(command, *options)
        self.assertEqual(code, 0, errors)
        return {item['path']: item['content'] for item in json.loads(output)['artifacts']}

    def test_composes_context_subagent_models_and_mcp(self):
        prefix = 'antigravity/sdk'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instructions = root / 'instructions.txt'
            instructions.write_text("Review 'quotes' and \\slashes.\n", encoding='utf-8')
            context = self.artifacts('bundle', '--profile', 'efficient', '--skill', 'benchmark-design')
            agent = self.artifacts('agent', '--name', 'reviewer', '--description', 'Review',
                                   '--instructions-file', str(instructions), '--model', 'gemini-3.1-pro',
                                   '--allow-skill', 'benchmark-design')
            mcp = self.artifacts('render', '--profile', 'efficient', '--enable', 'context7',
                                 '--antigravity-sdk-allow-tool', 'context7:query-docs')
            model = self.artifacts('model', '--model', 'gemini-3.1-pro',
                                   '--antigravity-sdk-endpoint', 'vertex', '--reasoning-effort', 'xhigh')
            modules = {}
            for package, types in {
                'google.antigravity': ('LocalAgentConfig', 'CapabilitiesConfig'),
                'google.antigravity.models': ('GeminiModelOptions', 'ModelTarget', 'VertexEndpoint'),
                'google.antigravity.types': ('McpStreamableHttpServer', 'SubagentConfig', 'SubagentCapabilities',
                                             'SubagentOverrideSkillsConfig'),
            }.items():
                module = modules[package] = ModuleType(package)
                for name in types:
                    setattr(module, name, SimpleNamespace)
                module.BuiltinTools = SimpleNamespace(VIEW_FILE='view_file', FINISH='finish',
                                                      INVOKE_SUBAGENT='invoke_subagent')

            for name, content in (context | agent | mcp | model).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')

            def constructor(path, name):
                file = root / prefix / path
                scope = {'__file__': str(file)}
                exec(compile(file.read_bytes(), file, 'exec'), scope)
                return scope[name]

            with patch.dict('sys.modules', modules):
                options = constructor('context/context_config.py', 'context_options')()
                child = constructor('agents/reviewer/subagent_config.py', 'build_subagent')()
                build_mcp = constructor('mcp_config.py', 'build_mcp_servers')
                config = constructor('model_config.py', 'build_config')(
                    **options, mcp_servers=build_mcp(), subagents=[child]
                )
            self.assertIn('Advisory profile guidance', config.system_instructions)
            root_skill = root / prefix / 'context/skills/benchmark-design'
            child_skill = root / prefix / 'agents/reviewer/skills/benchmark-design'
            self.assertEqual(config.skills_paths, [str(root_skill)])
            self.assertEqual(child.skills_config.skills_paths, [str(child_skill)])
            self.assertEqual((root_skill / 'SKILL.md').read_bytes(), (child_skill / 'SKILL.md').read_bytes())
            self.assertEqual(child.system_instructions, instructions.read_text(encoding='utf-8'))
            self.assertEqual(child.model, 'gemini-3.1-pro')
            self.assertEqual(child.tools, [])
            self.assertEqual(child.capabilities.enabled_tools, ['view_file', 'finish'])
            self.assertEqual(config.capabilities.enabled_tools, ['view_file', 'finish', 'invoke_subagent'])
            self.assertEqual(config.subagents, [child])
            self.assertEqual(config.mcp_servers[0].enabled_tools, ['query-docs'])
            self.assertEqual(config.model.endpoint.options.thinking_level, 'extra_high')
            self.assertTrue(config.vertex)

    def test_local_model_selection_stays_local_and_rejects_gemini_effort(self):
        for endpoint, port in [('ollama', 11434), ('lm-studio', 1234)]:
            with self.subTest(endpoint=endpoint):
                args = ('--antigravity-sdk-endpoint', endpoint, '--model', 'fixture:local')
                source = self.artifacts('model', *args)['antigravity/sdk/model_config.py']
                self.assertIn('LocalOpenAIAgentConfig', source)
                self.assertIn(f'http://localhost:{port}/v1', source)
                self.assertEqual(self.run_export('model', *args, '--reasoning-effort', 'high')[0], 2)
        self.assertEqual(self.run_export('model', '--model', 'fixture')[0], 2)

    def test_mcp_is_default_off_and_requires_an_exact_allowed_tool_per_server(self):
        args = ('--profile', 'efficient')
        document = self.artifacts('render', *args)['antigravity/sdk/mcp_config.py']
        scope = {}
        exec(compile(document, 'mcp_config.py', 'exec'), scope)
        self.assertEqual(scope['build_mcp_servers'](), [])
        for selectors in ([], ['context7:*'], ['different:lookup'], ['context7:query-docs', 'context7:query-docs']):
            with self.subTest(selectors=selectors):
                flags = [item for tool in selectors for item in ('--antigravity-sdk-allow-tool', tool)]
                self.assertEqual(self.run_export('render', *args, '--enable', 'context7', *flags)[0], 2)
