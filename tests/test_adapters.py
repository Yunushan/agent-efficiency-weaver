import json
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path

from agentweaver.adapters import render_mcp
from agentweaver.config import WeaverError, load_json
from agentweaver.integrations import get_integration

ROOT = Path(__file__).resolve().parents[1]


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_json(ROOT / 'catalogs/mcp-servers.json')
        self.profile = load_json(ROOT / 'profiles/efficient.json')
        self.golden = load_json(ROOT / 'tests/fixtures/adapters/golden.json')

    def test_all_native_default_surfaces_match_documented_goldens(self):
        for agent, fixture in self.golden.items():
            spec = get_integration(agent, ROOT)
            for enabled in ([], ['context7']):
                with self.subTest(agent=agent, enabled=enabled):
                    artifacts = render_mcp(spec, spec.default_surface, self.profile, self.catalog, enabled)
                    self.assertEqual(len(artifacts), 1)
                    artifact = artifacts[0]
                    self.assertEqual(artifact.relative_path.as_posix(), fixture['path'])
                    self.assertNotIn(b'\r', artifact.content)
                    expected = fixture['enabled' if enabled else 'empty']
                    if agent == 'codex':
                        self.assertEqual(artifact.content.decode('utf-8'), expected)
                        parsed = tomllib.loads(expected)
                        self.assertIn('mcp_servers', parsed)
                    else:
                        self.assertEqual(json.loads(artifact.content), expected)
                        self.assertEqual(artifact.content, (json.dumps(expected, indent=2, ensure_ascii=False) + '\n').encode())

    def test_unknown_dialect_cannot_silently_use_a_different_vendor_schema(self):
        spec = get_integration('kilo', ROOT)
        target = replace(spec.surface('cli'), mcp_dialect='future-unknown-format')
        altered = replace(spec, surfaces=(target,))
        with self.assertRaises(WeaverError):
            render_mcp(altered, 'cli', self.profile, self.catalog, [])

    def test_kilo_ide_uses_same_documented_native_dialect(self):
        spec = get_integration('kilo', ROOT)
        expected = self.golden['kilo']
        for enabled in ([], ['context7']):
            artifact = render_mcp(spec, 'ide', self.profile, self.catalog, enabled)[0]
            self.assertEqual(artifact.relative_path.as_posix(), expected['path'])
            self.assertEqual(json.loads(artifact.content), expected['enabled' if enabled else 'empty'])

    def test_user_scope_only_is_never_project_configuration(self):
        for agent in ('cline', 'openhands', 'zed', 'goose'):
            spec = get_integration(agent, ROOT)
            path = render_mcp(spec, spec.default_surface, self.profile, self.catalog, [])[0].relative_path
            self.assertIn('fragments', path.parts)

    def test_pi_normalized_name_collisions_are_rejected(self):
        spec = get_integration('pi', ROOT)
        catalog = {'servers': {name: dict(self.catalog['servers']['context7']) for name in ('my-tool', 'my_tool')}}
        profile = dict(self.profile, allowed_mcp=['my-tool', 'my_tool'], max_mcp_servers=2)
        with self.assertRaises(WeaverError):
            render_mcp(spec, 'cli', profile, catalog, ['my-tool', 'my_tool'])

    def test_unavailable_routes_and_surfaces_fail_closed(self):
        for agent in ('aider', 'openrouter'):
            spec = get_integration(agent, ROOT)
            with self.subTest(agent=agent), self.assertRaises(WeaverError):
                render_mcp(spec, spec.default_surface, self.profile, self.catalog, [])
        spec = get_integration('kilo', ROOT)
        with self.assertRaises(WeaverError):
            render_mcp(spec, 'not-real', self.profile, self.catalog, [])

    def test_enterprise_selection_and_unicode_validation(self):
        profile = load_json(ROOT / 'profiles/enterprise-secure.json')
        for agent in self.golden:
            spec = get_integration(agent, ROOT)
            with self.subTest(agent=agent), self.assertRaises(WeaverError):
                render_mcp(spec, spec.default_surface, profile, self.catalog, ['context7'])
        spec = get_integration('codex', ROOT)
        for url in ('https://example.com/\ud800', 'https://example.com/\n'):
            catalog = {'servers': {'context7': dict(self.catalog['servers']['context7'], url=url)}}
            with self.assertRaises(WeaverError):
                render_mcp(spec, 'cli', self.profile, catalog, ['context7'])

    def test_unicode_urls_are_encoded_without_host_line_endings(self):
        url = 'https://example.com/資料'
        catalog = {'servers': {'context7': dict(self.catalog['servers']['context7'], url=url)}}
        for agent in self.golden:
            spec = get_integration(agent, ROOT)
            with self.subTest(agent=agent):
                artifact = render_mcp(spec, spec.default_surface, self.profile, catalog, ['context7'])[0]
                text = artifact.content.decode('utf-8')
                parsed = tomllib.loads(text) if agent == 'codex' else json.loads(text)
                self.assertIsInstance(parsed, dict)
                self.assertIn(url, text)
                self.assertNotIn(b'\r', artifact.content)

    def test_goose_is_json_compatible_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('Optional development-only PyYAML not installed')
        spec = get_integration('goose', ROOT)
        content = render_mcp(spec, spec.default_surface, self.profile, self.catalog, ['context7'])[0].content
        self.assertEqual(yaml.safe_load(content), json.loads(content))
