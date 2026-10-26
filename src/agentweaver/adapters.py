"""Offline, documented MCP configuration dialects."""
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from .artifacts import RenderedArtifact
from .config import MCPSelection, StdioServer, WeaverError, select_servers
from .integrations import IntegrationSpec

_NAME, _URL = object(), object()
_JSON_DIALECTS: dict[str, tuple[str, tuple[tuple[str, Any], ...]]] = {
    'mcp.type-remote': ('mcp', (('type', 'remote'), ('url', _URL))),
    'mcpServers.url': ('mcpServers', (('url', _URL),)),
    'mcpServers.httpUrl': ('mcpServers', (('httpUrl', _URL),)),
    'mcpServers.type-streamableHttp': ('mcpServers', (('type', 'streamableHttp'), ('url', _URL), ('disabled', False))),
    'mcpServers.type-http': ('mcpServers', (('type', 'http'), ('url', _URL))),
    'mcpServers.type-sse': ('mcpServers', (('type', 'sse'), ('url', _URL))),
    'mcpServers.url-or-type-http': ('mcpServers', (('url', _URL),)),
    'mcpServers.transport-http': ('mcpServers', (('transport', 'http'), ('url', _URL))),
    'mcpServers.serverUrl': ('mcpServers', (('serverUrl', _URL),)),
    'servers.type-http': ('servers', (('type', 'http'), ('url', _URL))),
    'context_servers.url': ('context_servers', (('url', _URL),)),
    'amp.mcpServers.url': ('amp.mcpServers', (('url', _URL),)),
    'extensions.uri-streamable_http': ('extensions', (('type', 'streamable_http'), ('name', _NAME),
                                                    ('enabled', True), ('uri', _URL))),
    'mcp_servers.yaml-url-enabled': ('mcp_servers', (('url', _URL), ('enabled', True))),
}
_REQUEST_DIALECTS: dict[str, tuple[str, tuple[tuple[str, Any], ...]]] = {
    'tools.mcp-request': ('tools', (('type', 'mcp'), ('server_label', _NAME),
                                  ('server_url', _URL), ('require_approval', 'always'))),
    'integrations.ephemeral-mcp-request': ('integrations', (('type', 'ephemeral_mcp'),
                                                         ('server_label', _NAME), ('server_url', _URL))),
}
_USER_FRAGMENTS = {
    'cline': 'cline_mcp_settings.json', 'openhands': 'mcp.json',
    'zed': 'settings.json', 'goose': 'config.yaml',
    'hermes': 'config.yaml', 'lm-studio': 'mcp.json',
}
# UI-edited JSON has a documented schema but no documented persistent file path.
_APP_FRAGMENTS = {('qoder', 'ide'): ('mcpServers.type-sse', 'mcp.ide.json')}
_TOML_DIALECT = 'mcp_servers.toml-url'
# Product-specific documentation is required even when two JSON dialects look alike.
# Values are (registered dialect, documented stdio shape, first-party source).
_STDIO_ROUTES: dict[str, tuple[str, str, str]] = {
    'abacus': ('mcpServers.url', 'command-args', 'https://abacus.ai/help/abacusai-desktop/mcp-configuration'),
    'amp': ('amp.mcpServers.url', 'command-args', 'https://ampcode.com/docs/customize/mcp'),
    'antigravity': ('mcpServers.serverUrl', 'command-args', 'https://antigravity.google/docs/mcp'),
    'claude-code': ('mcpServers.type-http', 'command-args', 'https://code.claude.com/docs/en/mcp'),
    'cline': ('mcpServers.type-streamableHttp', 'command-args', 'https://docs.cline.bot/mcp/mcp-overview'),
    'codex': (_TOML_DIALECT, 'toml-command-args', 'https://learn.chatgpt.com/docs/extend/mcp'),
    'copilot': ('servers.type-http', 'type-stdio', 'https://code.visualstudio.com/docs/agents/reference/mcp-configuration'),
    'cursor': ('mcpServers.url', 'type-stdio', 'https://cursor.com/docs/mcp'),
    'devin': ('mcpServers.transport-http', 'command-args', 'https://docs.devin.ai/cli/extensibility/mcp/configuration'),
    'goose': ('extensions.uri-streamable_http', 'goose-stdio', 'https://goose-docs.ai/docs/guides/config-files/'),
    'junie': ('mcpServers.type-http', 'command-args', 'https://junie.jetbrains.com/docs/junie-cli-mcp-configuration.html'),
    'kilo': ('mcp.type-remote', 'local-command-array', 'https://kilo.ai/docs/automate/mcp/using-in-cli'),
    'kiro': ('mcpServers.url', 'command-args', 'https://kiro.dev/docs/mcp/configuration/'),
    'opencode': ('mcp.type-remote', 'local-command-array', 'https://opencode.ai/docs/mcp-servers/'),
    'openhands': ('mcpServers.url', 'command-args', 'https://github.com/OpenHands/docs/blob/main/openhands/usage/cli/mcp-servers.mdx'),
    'pi': ('mcpServers.url-or-type-http', 'command-args', 'https://pi.dev/docs/latest/mcp'),
    'qoder': ('mcpServers.type-http', 'command-args', 'https://docs.qoder.com/cli/mcp-reference'),
    'zed': ('context_servers.url', 'command-args', 'https://zed.dev/docs/ai/mcp'),
    'factory': ('mcpServers.type-http', 'type-stdio', 'https://docs.factory.com/harness/mcp'),
    'hermes': ('mcp_servers.yaml-url-enabled', 'hermes-stdio', 'https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp'),
    'kimi-code': ('mcpServers.url', 'command-args', 'https://moonshotai.github.io/kimi-code/en/customization/mcp.html'),
    'lm-studio': ('mcpServers.url', 'type-stdio', 'https://lmstudio.ai/docs/app/mcp'),
    'qwen-code': ('mcpServers.httpUrl', 'command-args', 'https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/'),
}
_STDIO_ADDITIONAL_ROUTES = {
    ('qoder', 'mcpServers.type-sse'): (
        'command-args', 'https://docs.qoder.com/user-guide/chat/model-context-protocol'
    ),
}


def stdio_inventory() -> dict[str, dict[str, Any]]:
    """Document adapter schemas separately from installed-host/runtime evidence."""
    inventory: dict[str, dict[str, Any]] = {product: {'mcp_dialect': dialect, 'schema': shape,
                      'sources': [source, 'https://cursor.com/docs/mcp'] if product == 'lm-studio' else [source],
                      'documentation_note': ('LM Studio explicitly documents Cursor mcp.json notation; '
                                             'this is documentation compatibility, not a runtime check.'
                                             if product == 'lm-studio' else 'First-party stdio configuration reference.'),
                      'checked_on': '2026-10-26', 'fixture_tests': ['tests/test_stdio_adapters.py'],
                      'runtime_checks': []}
            for product, (dialect, shape, source) in _STDIO_ROUTES.items()}
    inventory['openhands']['sources'].append(
        'https://github.com/OpenHands/OpenHands-CLI/blob/283c6ac3610f4299f405f7389f65766dcb83e12a/openhands_cli/mcp/mcp_utils.py#L15-L58'
    )
    inventory['openhands']['documentation_note'] = (
        'The CLI manual JSON format documents command/args in ~/.openhands/mcp.json; '
        'pinned CLI 1.16.0 source confirms file loading. This fragment declares no environment '
        'or credentials and performs no installed-client check.'
    )
    for (product, dialect), (shape, source) in _STDIO_ADDITIONAL_ROUTES.items():
        inventory[product]['additional_routes'] = [
            {'mcp_dialect': dialect, 'schema': shape, 'sources': [source],
             'checked_on': '2026-10-26', 'fixture_tests': ['tests/test_capability_expansion.py'],
             'runtime_checks': []}
        ]
    return inventory


def _stdio_entry(agent_id: str | None, dialect: str, name: str, selection: StdioServer) -> dict[str, Any]:
    route = _STDIO_ROUTES.get(agent_id or '')
    extra = _STDIO_ADDITIONAL_ROUTES.get((agent_id or '', dialect))
    if extra is not None:
        shape = extra[0]
    elif route is not None and route[0] == dialect:
        shape = route[1]
    else:
        raise WeaverError('Native stdio MCP export is not documented for this product/dialect; use manual setup')
    command, args = selection['command'], list(selection['args'])
    if shape == 'local-command-array':
        return {'type': 'local', 'command': [command, *args], 'enabled': True}
    if shape == 'goose-stdio':
        return {'type': 'stdio', 'name': name, 'enabled': True, 'cmd': command, 'args': args}
    entry: dict[str, Any] = {'command': command, 'args': args}
    if shape == 'type-stdio':
        entry = {'type': 'stdio', **entry}
    elif shape == 'hermes-stdio':
        entry['enabled'] = True
    return entry


def json_bytes(data: Any) -> bytes:
    try:
        return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WeaverError('Cannot serialize artifact') from exc


def _json_document(dialect: str, selected: Mapping[str, MCPSelection], agent_id: str | None = None) -> dict[str, Any]:
    if dialect not in _JSON_DIALECTS:
        raise WeaverError('No documented exporter for this MCP dialect; inspect inventory')
    root_key, fields = _JSON_DIALECTS[dialect]
    return {root_key: {name: ({key: selection if value is _URL else name if value is _NAME else value
                              for key, value in fields} if isinstance(selection, str) else
                             _stdio_entry(agent_id, dialect, name, selection))
                       for name, selection in selected.items()}}


def mcp_document(agent_id: str, selected: Mapping[str, MCPSelection]) -> dict[str, Any]:
    dialect = {'kilo': 'mcp.type-remote', 'abacus': 'mcpServers.url'}.get(agent_id)
    if dialect is None:
        raise WeaverError('Legacy rendering accepts kilo or abacus; use the registry-backed exporter')
    return _json_document(dialect, selected, agent_id)


def _request_document(dialect: str, selected: Mapping[str, MCPSelection]) -> dict[str, Any]:
    if any(not isinstance(selection, str) for selection in selected.values()):
        raise WeaverError('This MCP API request supports remote HTTPS servers only; stdio is unavailable')
    root_key, fields = _REQUEST_DIALECTS[dialect]
    return {root_key: [{key: url if value is _URL else name if value is _NAME else value
                        for key, value in fields} for name, url in selected.items()]}


def render_mcp(spec: IntegrationSpec, surface: str, profile: dict[str, Any],
               catalog: dict[str, Any], enabled: list[str]) -> list[RenderedArtifact]:
    if spec.kind not in ('agent', 'hybrid'):
        raise WeaverError('Provider gateways cannot host MCP; use the provider command')
    target = spec.surface(surface)
    dialect = target.mcp_dialect
    if (dialect not in (*_JSON_DIALECTS, *_REQUEST_DIALECTS, _TOML_DIALECT)
            or target.capabilities['mcp'].state not in ('available', 'manual')):
        raise WeaverError('Native MCP export unavailable on this surface; use bundle for instruction handoff')
    selected = select_servers(profile, catalog, enabled)
    if spec.id == 'pi':
        normalized = [name.replace('-', '_') for name in selected]
        if len(normalized) != len(set(normalized)):
            raise WeaverError('Pi MCP names collide after hyphen/underscore normalization')
    if dialect in _REQUEST_DIALECTS:
        if target.config_scope != 'request':
            raise WeaverError('MCP request dialect requires request scope')
        path = PurePosixPath(spec.id, 'requests', f'{target.id}.json')
    elif target.config_scope == 'user':
        if spec.id not in _USER_FRAGMENTS:
            raise WeaverError('No documented safe user-scope fragment target')
        path = PurePosixPath(spec.id, 'fragments', _USER_FRAGMENTS[spec.id])
    elif target.config_scope == 'app':
        fragment = _APP_FRAGMENTS.get((spec.id, target.id))
        if fragment is None or fragment[0] != dialect:
            raise WeaverError('No documented safe app-scope MCP fragment target')
        path = PurePosixPath(spec.id, 'fragments', fragment[1])
    else:
        if target.config_scope != 'project' or not target.native_path_hint:
            raise WeaverError('Native project export has no documented safe target')
        path = PurePosixPath(spec.id, target.native_path_hint)
    if dialect in _REQUEST_DIALECTS:
        content = json_bytes(_request_document(dialect, selected))
    elif dialect == _TOML_DIALECT:
        if selected:
            text = '\n'.join(
                _toml_server(spec.id, name, selection) for name, selection in selected.items()
            )
        else:
            text = '[mcp_servers]\n'
        content = text.encode('utf-8')
    else:
        if not isinstance(dialect, str):
            raise WeaverError('MCP dialect is not documented')
        content = json_bytes(_json_document(dialect, selected, spec.id))
    kind = ('mcp-request' if target.config_scope == 'request' else
            'mcp-fragment' if target.config_scope in ('user', 'app') else 'mcp')
    return [RenderedArtifact(path, content, kind)]


def _toml_server(agent_id: str, name: str, selection: MCPSelection) -> str:
    heading = f'[mcp_servers.{json.dumps(name, ensure_ascii=False)}]\n'
    if isinstance(selection, str):
        return heading + f'url = {json.dumps(selection, ensure_ascii=False)}\n'
    entry = _stdio_entry(agent_id, _TOML_DIALECT, name, selection)
    return (heading + f'command = {json.dumps(entry["command"], ensure_ascii=False)}\n'
            + f'args = {json.dumps(entry["args"], ensure_ascii=False)}\n')
