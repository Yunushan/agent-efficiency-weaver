import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentweaver.config import WeaverError, load_json, render, validate_catalog, validate_profile
from agentweaver.integrations import load_integrations

ROOT = Path(__file__).resolve().parents[1]

class ValidationTests(unittest.TestCase):
    def test_catalog(self):
        catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        self.assertEqual(validate_catalog(catalog), [])
    def test_profiles(self):
        catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        for path in (ROOT / "profiles").glob("*.json"):
            self.assertEqual(validate_profile(load_json(path), catalog), [])

    def test_invalid_json_is_safe(self):
        for text in ('[]', 'null', '{"x":1,"x":2}', '{"x": NaN}', '{"x": 1e9999}', '{broken'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'bad.json'
                path.write_text(text, encoding='utf-8')
                with self.assertRaises(WeaverError):
                    load_json(path)

    def test_parser_limits_are_reported_as_weaver_errors(self):
        for error in (ValueError('NEVER-ECHO'), RecursionError('NEVER-ECHO')):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'bounded.json'
                path.write_text('{"x": 1}', encoding='utf-8')
                with patch('agentweaver.config.json.loads', side_effect=error):
                    with self.assertRaises(WeaverError) as raised:
                        load_json(path)
                self.assertNotIn('NEVER-ECHO', str(raised.exception))

    def test_invalid_profiles_fail_closed(self):
        catalog = load_json(ROOT / 'catalogs/mcp-servers.json')
        profile = load_json(ROOT / 'profiles/efficient.json')
        changes = [
            {'policy': None}, {'policy': []}, {'policy': True},
            {'policy': {'allow_production_writes': False, 'auto_approve': True}},
            {'allowed_mcp': [None]}, {'allowed_mcp': [[]]},
            {'allowed_mcp': ['context7', 'context7']}, {'allowed_mcp': 'context7'},
            {'max_mcp_servers': True}, {'max_mcp_servers': -1},
            {'id': 'enterprise-secure', 'max_mcp_servers': 1, 'allowed_mcp': ['context7']},
            {'id': '../private'}, {'id': '\ud800'},
            {'retrieval': {'max_snippets': True, 'avoid_full_repo_dumps': True}},
            {'retrieval': {'max_snippets': 0, 'avoid_full_repo_dumps': True}},
            {'retrieval': {'max_snippets': 8, 'avoid_full_repo_dumps': 'yes'}},
        ]
        for change in changes:
            with self.subTest(change=repr(change)):
                invalid = dict(profile, **change)
                self.assertTrue(validate_profile(invalid, catalog))
                with self.assertRaises(WeaverError):
                    render('kilo', invalid, catalog, [])
        for invalid in (None, [], 'bad'):
            with self.subTest(invalid=invalid):
                self.assertTrue(validate_profile(invalid, catalog))
                self.assertTrue(validate_catalog(invalid))

    def test_invalid_servers_and_secret_urls_fail_closed(self):
        catalog = load_json(ROOT / 'catalogs/mcp-servers.json')
        for url in ('http://example.com', 'https://user:secret@example.com/mcp',
                    'https://example.com?token=secret', 'https://example.com#secret',
                    'https://example.com:bad/mcp', 'https://example.com/\nsecret',
                    'https://example.com/\ud800', None, [], 3):
            with self.subTest(url=repr(url)):
                invalid = copy.deepcopy(catalog)
                invalid['servers']['context7']['url'] = url
                self.assertTrue(validate_catalog(invalid))
                profile = load_json(ROOT / 'profiles/efficient.json')
                with self.assertRaises(WeaverError) as raised:
                    render('kilo', profile, invalid, [])
                self.assertNotIn('secret', str(raised.exception))
        for changes in ({'kind': 'stdio'}, {'default_enabled': True},
                        {'url': 'https://example.com', 'headers': {'Authorization': 'secret'}}):
            invalid = copy.deepcopy(catalog)
            invalid['servers']['context7'].update(changes)
            self.assertTrue(validate_catalog(invalid))

    def test_registry_evidence_urls_and_unknown_fields_cannot_leak_credentials(self):
        original = load_json(ROOT / 'catalogs/integrations.json')
        bad_urls = ('https://user:NEVER-ECHO@example.com/docs',
                    'https://example.com/docs?api_key=NEVER-ECHO',
                    'https://example.com/docs#access_token=NEVER-ECHO',
                    'https://example.com:bad/docs')
        for url in bad_urls:
            data = copy.deepcopy(original)
            data['integrations'][0]['surfaces'][0]['capabilities']['mcp']['sources'] = [url]
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'catalogs').mkdir()
                (root / 'catalogs/integrations.json').write_text(json.dumps(data), encoding='utf-8')
                with self.subTest(url=url):
                    with self.assertRaises(WeaverError) as raised:
                        load_integrations(root)
                    self.assertNotIn('NEVER-ECHO', str(raised.exception))
        original['NEVER-ECHO'] = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'catalogs').mkdir()
            (root / 'catalogs/integrations.json').write_text(json.dumps(original), encoding='utf-8')
            with self.assertRaises(WeaverError) as raised:
                load_integrations(root)
            self.assertNotIn('NEVER-ECHO', str(raised.exception))

    def test_malformed_selections_raise_weaver_error(self):
        catalog = load_json(ROOT / 'catalogs/mcp-servers.json')
        profile = load_json(ROOT / 'profiles/efficient.json')
        for enabled in (None, 'context7', [None], [[]], ['unknown'], ['context7', 'context7']):
            with self.subTest(enabled=enabled), self.assertRaises(WeaverError):
                render('kilo', profile, catalog, enabled)

    def test_enterprise_never_selects_mcp(self):
        catalog = load_json(ROOT / 'catalogs/mcp-servers.json')
        profile = load_json(ROOT / 'profiles/enterprise-secure.json')
        self.assertEqual(render('kilo', profile, catalog, []), {'mcp': {}})
        with self.assertRaises(WeaverError):
            render('kilo', profile, catalog, ['context7'])
