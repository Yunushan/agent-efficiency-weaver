"""Offline, documented MCP configuration dialects."""
import json
import re
import shlex
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
    'mcpServers.jetbrains-air-ide': ('mcpServers', (('url', _URL),)),
    'mcpServers.httpUrl': ('mcpServers', (('httpUrl', _URL),)),
    'mcpServers.type-streamableHttp': ('mcpServers', (('type', 'streamableHttp'), ('url', _URL))),
    'mcpServers.type-http': ('mcpServers', (('type', 'http'), ('url', _URL))),
    'mcpServers.copilot-portable': ('mcpServers', (('type', 'http'), ('url', _URL), ('tools', ['*']))),
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
    'jetbrains-air': 'mcp.ide.json',
}
_LM_STUDIO_RESPONSES_FIELDS = (('type', 'mcp'), ('server_label', _NAME), ('server_url', _URL))
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
    'jetbrains-air': ('mcpServers.jetbrains-air-ide', 'command-args',
                      'https://www.jetbrains.com/help/air/air-in-ides-extend-agent-capabilities.html'),
    'kilo': ('mcp.type-remote', 'local-command-array', 'https://kilo.ai/docs/automate/mcp/using-in-cli'),
    'kiro': ('mcpServers.url', 'command-args', 'https://kiro.dev/docs/mcp/configuration/'),
    'opencode': ('mcp.type-remote', 'local-command-array', 'https://opencode.ai/docs/migrate-v1#mcp-servers'),
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
    ('antigravity', 'sdk.mcp_servers'): (
        'McpStdioServer', 'https://antigravity.google/docs/sdk/mcp/',
        '2026-11-15', 'tests/test_antigravity_sdk.py',
    ),
    ('openhands', 'openhands-canvas.settings-mcp'): (
        'transport-stdio-disabled', 'https://github.com/OpenHands/OpenHands/blob/v1.40.0/src/utils/mcp-config.ts',
        '2026-11-14', 'tests/test_stdio_adapters.py',
    ),
    ('copilot', 'mcpServers.copilot-portable'): (
        'copilot-portable-stdio', 'https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers',
        '2026-11-13', 'tests/test_stdio_adapters.py',
    ),
    ('jetbrains-air', 'mcpServers.url'): (
        'command-args-disabled', 'https://data.services.jetbrains.com/products?code=AIR',
        '2026-11-15', 'tests/test_air_ide.py',
    ),
    ('opencode', 'mcp.servers.type-remote'): (
        'local-command-array-disabled', 'https://opencode.ai/docs/mcp-servers/',
        '2026-10-27', 'tests/test_stdio_adapters.py',
    ),
    ('qoder', 'mcpServers.type-sse'): (
        'command-args', 'https://docs.qoder.com/user-guide/chat/model-context-protocol',
        '2026-10-26', 'tests/test_capability_expansion.py',
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
    inventory['jetbrains-air'].update(checked_on='2026-11-14', fixture_tests=['tests/test_air_ide.py'])
    for (product, dialect), (shape, source, checked_on, test) in _STDIO_ADDITIONAL_ROUTES.items():
        inventory[product]['additional_routes'] = [
            {'mcp_dialect': dialect, 'schema': shape, 'sources': [source],
             'checked_on': checked_on, 'fixture_tests': [test],
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
    if shape == 'local-command-array-disabled':
        return {'type': 'local', 'command': [command, *args], 'disabled': True}
    if shape == 'goose-stdio':
        return {'type': 'stdio', 'name': name, 'enabled': True, 'cmd': command, 'args': args}
    if shape == 'copilot-portable-stdio':
        return {'type': 'stdio', 'command': command, 'args': args, 'tools': ['*']}
    if shape == 'transport-stdio-disabled':
        return {'transport': 'stdio', 'command': command, 'args': args, 'enabled': False}
    entry: dict[str, Any] = {'command': command, 'args': args}
    if shape == 'type-stdio':
        entry = {'type': 'stdio', **entry}
    elif shape == 'command-args-disabled':
        entry['enabled'] = False
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


def hermes_profile_mcp(profile: dict[str, Any], catalog: dict[str, Any], servers: list[str]) -> dict[str, Any]:
    selected = select_servers(profile, catalog, servers)
    document = _json_document('mcp_servers.yaml-url-enabled', selected, 'hermes')
    for entry in document['mcp_servers'].values():
        entry['enabled'] = False
    return document


def lmstudio_stdio_fragments(
    name: str, command: str, args: list[str], tools: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected: dict[str, MCPSelection] = {name: {"kind": "stdio", "command": command, "args": args}}
    configuration = _json_document("mcpServers.url", selected, "lm-studio")
    request = _request_document("integrations.ephemeral-mcp-request", selected, lmstudio_plugins=True,
                                lmstudio_tools=[f"{name}:{tool}" for tool in tools])
    return configuration, request


def _apply_tool_allowlist(entries: dict[str, dict[str, Any]], selectors: list[str], option: str, product: str) -> None:
    for selector in selectors:
        server, _, tool = selector.partition(':')
        if server not in entries or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', tool) is None:
            raise WeaverError(f'Use {option} SERVER:TOOL with an exact tool name on a selected server')
        tools = entries[server].setdefault('allowed_tools', [])
        if tool in tools:
            raise WeaverError(f'Duplicate {product} allowed tool selection')
        tools.append(tool)


def _request_document(dialect: str, selected: Mapping[str, MCPSelection], *,
                      lmstudio_responses: bool = False, lmstudio_plugins: bool = False,
                      lmstudio_tools: list[str] | None = None, perplexity_tools: list[str] | None = None,
                      perplexity_deferred_servers: list[str] | None = None) -> dict[str, Any]:
    if not lmstudio_plugins and any(not isinstance(selection, str) for selection in selected.values()):
        raise WeaverError('This MCP API request supports remote HTTPS servers only; stdio is unavailable')
    root_key, fields = _REQUEST_DIALECTS[dialect]
    if lmstudio_responses:
        root_key, fields = 'tools', _LM_STUDIO_RESPONSES_FIELDS
    entries: dict[str, dict[str, Any]]
    if lmstudio_plugins:
        entries = {name: {'type': 'plugin', 'id': f'mcp/{name}'} for name in selected}
    else:
        entries = {name: {key: url if value is _URL else name if value is _NAME else value for key, value in fields}
                   for name, url in selected.items()}
    if lmstudio_tools:
        _apply_tool_allowlist(entries, lmstudio_tools, '--lmstudio-allow-tool', 'LM Studio')
        if any('allowed_tools' not in entry for entry in entries.values()):
            raise WeaverError('Select an exact tool for each selected LM Studio server when using --lmstudio-allow-tool')
    if perplexity_tools:
        _apply_tool_allowlist(entries, perplexity_tools, '--perplexity-allow-tool', 'Perplexity')
    for server in perplexity_deferred_servers or ():
        if server not in entries:
            raise WeaverError('--perplexity-defer-tools requires a selected MCP server')
        entries[server]['defer_loading'] = True
    return {root_key: list(entries.values())}


def _form_values(lines: list[str], name: str, fields: dict[str, Any]) -> None:
    # Escape backticks in user-supplied strings to keep the Markdown fence intact.
    values = json.dumps(fields, indent=2, ensure_ascii=False).replace('`', r'\u0060')
    lines.extend(['', f'## {name}', '', '```json', values, '```'])


def _perplexity_app_mcp(selected: Mapping[str, MCPSelection]) -> RenderedArtifact:
    lines = ['# Perplexity custom MCP setup', '',
             'Open Plugins > Add custom. For an account remote connector, choose Add MCP connector and Remote.',
             'Organization-wide remote connectors are managed by administrators in Organization settings > Connectors.',
             'Review the connector and its tools before adding or enabling it. Enter any credentials separately.']
    if not selected:
        lines.extend(['', 'No MCP servers were selected.'])
    for name, selection in selected.items():
        if isinstance(selection, str):
            if len(name) > 64:
                raise WeaverError('Perplexity remote connector names must be at most 64 characters')
            _form_values(lines, name, {'Name': name, 'MCP server URL': selection})
            lines.extend(['', 'Choose the transport and authentication required by the server in Advanced settings.'])
        else:
            argv = [selection['command'], *selection['args']]
            _form_values(lines, name, {'Server Name': name, 'Command': shlex.join(argv)})
            lines.extend(['', 'Use the Simple tab in the Perplexity macOS app with the PerplexityXPC helper installed.',
                          'The command is shown using POSIX quoting. Review it and confirm the app accepts the '
                          'arguments as intended before saving.'])
    return RenderedArtifact(PurePosixPath('perplexity', 'mcp', 'app-setup.md'),
                            ('\n'.join(lines) + '\n').encode('utf-8'), 'mcp-setup')


def _apply_disabled_tools(servers: dict[str, Any], selectors: list[str], product: str) -> None:
    for selector in selectors:
        server, _, tool = selector.partition(':')
        if server not in servers or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', tool) is None:
            raise WeaverError(f'Use --{product.lower()}-disable-tool SERVER:TOOL with an exact tool name '
                              'on a selected server')
        tools = servers[server].setdefault('disabledTools', [])
        if tool in tools:
            raise WeaverError(f'Duplicate {product} disabled tool selection')
        tools.append(tool)


def render_mcp(spec: IntegrationSpec, surface: str, profile: dict[str, Any],
               catalog: dict[str, Any], enabled: list[str], *, opencode_v2: bool = False,
               copilot_portable: bool = False, antigravity_user_mcp: bool = False,
               devin_stage_disabled: bool = False, devin_disabled_tools: list[str] | None = None,
               kiro_disabled_tools: list[str] | None = None,
               lmstudio_responses: bool = False, lmstudio_plugins: bool = False,
               lmstudio_tools: list[str] | None = None, perplexity_tools: list[str] | None = None,
               perplexity_deferred_servers: list[str] | None = None,
               antigravity_sdk_tools: list[str] | None = None) -> list[RenderedArtifact]:
    if spec.kind not in ('agent', 'hybrid'):
        raise WeaverError('Provider gateways cannot host MCP; use the provider command')
    target = spec.surface(surface)
    dialect = target.mcp_dialect
    sdk_target = (spec.id, surface) == ('antigravity', 'sdk')
    if antigravity_sdk_tools and not sdk_target:
        raise WeaverError('Antigravity SDK tool allowlists require the SDK surface')
    if (lmstudio_responses or lmstudio_plugins or lmstudio_tools) and (spec.id, surface, dialect, target.config_scope) != (
            'lm-studio', 'api', 'integrations.ephemeral-mcp-request', 'request'):
        raise WeaverError('LM Studio MCP request options require the LM Studio API target')
    if (perplexity_tools or perplexity_deferred_servers) and (spec.id, surface, dialect, target.config_scope) != (
            'perplexity', 'agent-api', 'tools.mcp-request', 'request'):
        raise WeaverError('Perplexity MCP request options require the Perplexity Agent API target')
    if lmstudio_plugins and lmstudio_responses:
        raise WeaverError('Preconfigured LM Studio MCP plugins require the native chat request')
    if devin_stage_disabled or devin_disabled_tools:
        if (spec.id, surface, dialect, target.config_scope, target.native_path_hint) != (
                'devin', 'desktop-local', 'mcpServers.transport-http', 'project', '.devin/mcp_config.json'):
            raise WeaverError('Devin MCP controls require the Devin Desktop Local project target')
        if not enabled:
            raise WeaverError('Devin MCP controls require at least one server selected with --enable')
    if kiro_disabled_tools:
        if ((spec.id, dialect, target.config_scope, target.native_path_hint) !=
                ('kiro', 'mcpServers.url', 'project', '.kiro/settings/mcp.json') or surface not in {'cli', 'ide'}):
            raise WeaverError('Kiro MCP controls require a Kiro CLI or IDE project target')
        if not enabled:
            raise WeaverError('Kiro MCP controls require at least one server selected with --enable')
    if opencode_v2 and (spec.id, surface, dialect, target.config_scope, target.native_path_hint) != (
            'opencode', 'cli', 'mcp.type-remote', 'project', 'opencode.json'):
        raise WeaverError('OpenCode V2 MCP staging requires the OpenCode CLI project target')
    if copilot_portable and (opencode_v2 or (spec.id, surface, dialect, target.config_scope, target.native_path_hint) != (
            'copilot', 'vscode', 'servers.type-http', 'project', '.vscode/mcp.json')):
        raise WeaverError('Portable Copilot MCP staging requires the VS Code Copilot project target')
    if antigravity_user_mcp and (opencode_v2 or copilot_portable or spec.id != 'antigravity'
                                 or surface not in {'desktop', 'ide'} or dialect != 'mcpServers.serverUrl'):
        raise WeaverError('Antigravity user MCP staging requires the Desktop or IDE surface')
    if (sdk_target and dialect == 'sdk.mcp_servers' and target.capabilities['mcp'].state == 'manual'
            and target.capabilities['mcp'].sources):
        from .antigravity_sdk import SDK_PREFIX

        entries = {name: {'url': server} if isinstance(server, str) else
                   {'command': server['command'], 'args': server['args']}
                   for name, server in select_servers(profile, catalog, enabled).items()}
        _apply_tool_allowlist(entries, antigravity_sdk_tools or [], '--antigravity-sdk-allow-tool', 'Antigravity SDK')
        if any('allowed_tools' not in fields for fields in entries.values()):
            raise WeaverError('Select at least one exact tool for each Antigravity SDK MCP server')
        if any(re.fullmatch(r'[A-Za-z0-9_-]+', name) is None for name in entries):
            raise WeaverError('Antigravity SDK MCP server names require letters, digits, hyphens or underscores')
        lines = ['"""Construct reviewed MCP server entries without connecting or starting processes."""']
        if entries:
            constructors = sorted({'McpStreamableHttpServer' if 'url' in fields else 'McpStdioServer'
                                   for fields in entries.values()})
            lines.append('from google.antigravity.types import ' + ', '.join(constructors))
        lines.extend(['', '', 'def build_mcp_servers():', '    return ['])
        for name, entry in entries.items():
            constructor = 'McpStreamableHttpServer' if 'url' in entry else 'McpStdioServer'
            arguments = ', '.join(f'{key}={value!r}' for key, value in entry.items() if key != 'allowed_tools')
            lines.append(f'        {constructor}(name={name!r}, {arguments}, enabled_tools={entry["allowed_tools"]!r}),')
        lines.extend(['    ]', ''])
        return [RenderedArtifact(SDK_PREFIX / 'mcp_config.py', '\n'.join(lines).encode(), 'mcp-fragment')]
    if (spec.id, target.id, target.config_scope, target.documented_location) == (
        'perplexity', 'app', 'app', 'Plugins > Add custom',
    ) and target.capabilities['mcp'].state == 'manual' and dialect is None:
        return [_perplexity_app_mcp(select_servers(profile, catalog, enabled))]
    if (spec.id, target.id, target.config_scope, target.documented_location) == (
        'lm-studio', 'bionic', 'app', 'Settings > MCP > Add custom MCP',
    ) and target.capabilities['mcp'].state == 'manual' and dialect is None:
        selected = select_servers(profile, catalog, enabled)
        lines = ['# LM Studio Bionic custom MCP setup', '',
                 'In Settings > MCP, open Add custom MCP for each reviewed server.',
                 'Review the server and tools before saving. Saving can connect to the URL or start the command.',
                 'To save an entry paused, turn it off before selecting Add MCP.',
                 'This handoff contains values for the form. Weaver uses no credentials and opens no connections.']
        if not selected:
            lines.extend(['', 'No MCP servers were selected.'])
        for name, selection in selected.items():
            fields: dict[str, Any] = {'Name': name}
            if isinstance(selection, str):
                fields.update({'Connection': 'Web address', 'URL': selection})
            else:
                if '' in selection['args']:
                    raise WeaverError('Bionic drops empty MCP arguments in its custom server form')
                fields.update({'Connection': 'On this computer', 'Command': selection['command'],
                               'Arguments': selection['args']})
            _form_values(lines, name, fields)
            if isinstance(selection, str):
                lines.extend(['', 'Review Authentication for the endpoint. Automatic is the default. '
                              'Bionic prompts for browser sign-in when the server requires it. '
                              'For Access token, supply the token separately in Bionic.'])
            elif selection['args']:
                lines.extend(['', 'Turn on Show advanced options, then use Add argument to enter each '
                              'argument in its own field in the order shown.'])
        return [RenderedArtifact(PurePosixPath(spec.id, 'mcp', 'bionic-setup.md'),
                                 ('\n'.join(lines) + '\n').encode('utf-8'), 'mcp-setup')]
    if (dialect not in (*_JSON_DIALECTS, *_REQUEST_DIALECTS, _TOML_DIALECT, 'openhands-canvas.settings-mcp')
            or target.capabilities['mcp'].state not in ('available', 'manual')):
        raise WeaverError('Native MCP export unavailable on this surface; use bundle for instruction handoff')
    selected = select_servers(profile, catalog, enabled)
    if antigravity_user_mcp:
        return [RenderedArtifact(PurePosixPath(spec.id, 'fragments', f'mcp.{surface}.user.json'),
                                 json_bytes(_json_document(dialect, selected, spec.id)), 'mcp-fragment')]
    if dialect == 'openhands-canvas.settings-mcp':
        if ((spec.id, surface, target.config_scope) != ('openhands', 'canvas', 'request')
                or target.native_path_hint != 'requests/canvas'):
            raise WeaverError('OpenHands MCP settings requests require the Agent Canvas surface')
        return [
            RenderedArtifact(
                PurePosixPath(spec.id, target.native_path_hint, f'{name}.json'),
                json_bytes({'transport': 'http', 'url': selection, 'enabled': False}
                           if isinstance(selection, str) else _stdio_entry(spec.id, dialect, name, selection)),
                'mcp-request',
            )
            for name, selection in selected.items()
        ]
    if copilot_portable:
        document = _json_document('mcpServers.copilot-portable', selected, spec.id)
        return [RenderedArtifact(PurePosixPath(spec.id, '.mcp.json'), json_bytes(document), 'mcp')]
    if spec.id == 'pi':
        normalized = [name.replace('-', '_') for name in selected]
        if len(normalized) != len(set(normalized)):
            raise WeaverError('Pi MCP names collide after hyphen/underscore normalization')
    if opencode_v2:
        servers = {
            name: ({'type': 'remote', 'url': selection, 'disabled': True} if isinstance(selection, str)
                   else _stdio_entry(spec.id, 'mcp.servers.type-remote', name, selection))
            for name, selection in selected.items()
        }
        return [RenderedArtifact(PurePosixPath('opencode', 'opencode.json'),
                                 json_bytes({'mcp': {'servers': servers}}), 'mcp')]
    if dialect in _REQUEST_DIALECTS:
        if target.config_scope != 'request':
            raise WeaverError('MCP request dialect requires request scope')
        filename = 'responses.json' if lmstudio_responses else 'chat-plugins.json' if lmstudio_plugins else f'{target.id}.json'
        path = PurePosixPath(spec.id, 'requests', filename)
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
        content = json_bytes(_request_document(dialect, selected, lmstudio_responses=lmstudio_responses,
                                               lmstudio_plugins=lmstudio_plugins, lmstudio_tools=lmstudio_tools,
                                               perplexity_tools=perplexity_tools,
                                               perplexity_deferred_servers=perplexity_deferred_servers))
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
        document = _json_document(dialect, selected, spec.id)
        if devin_stage_disabled or spec.id == 'cline' or (spec.id, surface) == ('kiro', 'cli'):
            for entry in document['mcpServers'].values():
                entry['disabled'] = True
        if devin_disabled_tools:
            _apply_disabled_tools(document['mcpServers'], devin_disabled_tools, 'Devin')
        if kiro_disabled_tools:
            _apply_disabled_tools(document['mcpServers'], kiro_disabled_tools, 'Kiro')
        if spec.id == 'kilo' or (spec.id, surface) in {('zed', 'ide'), ('openhands', 'cli')}:
            for entry in next(iter(document.values())).values():
                entry['enabled'] = False
        content = json_bytes(document)
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
