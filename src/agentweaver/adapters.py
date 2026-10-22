"""Offline, documented MCP configuration dialects."""
import json
from pathlib import PurePosixPath
from typing import Any

from .artifacts import RenderedArtifact
from .config import WeaverError, select_servers
from .integrations import IntegrationSpec

_NAME, _URL = object(), object()
_JSON_DIALECTS: dict[str, tuple[str, tuple[tuple[str, Any], ...]]] = {
    'mcp.type-remote': ('mcp', (('type', 'remote'), ('url', _URL))),
    'mcpServers.url': ('mcpServers', (('url', _URL),)),
    'mcpServers.type-streamableHttp': ('mcpServers', (('type', 'streamableHttp'), ('url', _URL), ('disabled', False))),
    'mcpServers.type-http': ('mcpServers', (('type', 'http'), ('url', _URL))),
    'mcpServers.url-or-type-http': ('mcpServers', (('url', _URL),)),
    'mcpServers.transport-http': ('mcpServers', (('transport', 'http'), ('url', _URL))),
    'mcpServers.serverUrl': ('mcpServers', (('serverUrl', _URL),)),
    'servers.type-http': ('servers', (('type', 'http'), ('url', _URL))),
    'context_servers.url': ('context_servers', (('url', _URL),)),
    'amp.mcpServers.url': ('amp.mcpServers', (('url', _URL),)),
    'extensions.uri-streamable_http': ('extensions', (('type', 'streamable_http'), ('name', _NAME),
                                                    ('enabled', True), ('uri', _URL))),
}
_USER_FRAGMENTS = {
    'cline': 'cline_mcp_settings.json', 'openhands': 'mcp.json',
    'zed': 'settings.json', 'goose': 'config.yaml',
}
_TOML_DIALECT = 'mcp_servers.toml-url'


def json_bytes(data: Any) -> bytes:
    try:
        return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WeaverError('Cannot serialize artifact') from exc


def _json_document(dialect: str, selected: dict[str, str]) -> dict[str, Any]:
    if dialect not in _JSON_DIALECTS:
        raise WeaverError('No documented exporter for this MCP dialect; inspect inventory')
    root_key, fields = _JSON_DIALECTS[dialect]
    return {root_key: {name: {key: url if value is _URL else name if value is _NAME else value
                             for key, value in fields} for name, url in selected.items()}}


def mcp_document(agent_id: str, selected: dict[str, str]) -> dict[str, Any]:
    dialect = {'kilo': 'mcp.type-remote', 'abacus': 'mcpServers.url'}.get(agent_id)
    if dialect is None:
        raise WeaverError('Legacy rendering accepts kilo or abacus; use the registry-backed exporter')
    return _json_document(dialect, selected)


def render_mcp(spec: IntegrationSpec, surface: str, profile: dict[str, Any],
               catalog: dict[str, Any], enabled: list[str]) -> list[RenderedArtifact]:
    if spec.kind != 'agent':
        raise WeaverError('Provider gateways cannot host MCP; use the provider command')
    target = spec.surface(surface)
    dialect = target.mcp_dialect
    if (dialect not in (*_JSON_DIALECTS, _TOML_DIALECT)
            or target.capabilities['mcp'].state not in ('available', 'manual')):
        raise WeaverError('Native MCP export unavailable on this surface; use bundle for instruction handoff')
    selected = select_servers(profile, catalog, enabled)
    if spec.id == 'pi':
        normalized = [name.replace('-', '_') for name in selected]
        if len(normalized) != len(set(normalized)):
            raise WeaverError('Pi MCP names collide after hyphen/underscore normalization')
    if target.config_scope == 'user':
        if spec.id not in _USER_FRAGMENTS:
            raise WeaverError('No documented safe user-scope fragment target')
        path = PurePosixPath(spec.id, 'fragments', _USER_FRAGMENTS[spec.id])
    else:
        if target.config_scope != 'project' or not target.native_path_hint:
            raise WeaverError('Native project export has no documented safe target')
        path = PurePosixPath(spec.id, target.native_path_hint)
    if dialect == _TOML_DIALECT:
        if selected:
            text = '\n'.join(
                f'[mcp_servers.{json.dumps(name, ensure_ascii=False)}]\n'
                f'url = {json.dumps(url, ensure_ascii=False)}\n'
                for name, url in selected.items()
            )
        else:
            text = '[mcp_servers]\n'
        content = text.encode('utf-8')
    else:
        if not isinstance(dialect, str):
            raise WeaverError('MCP dialect is not documented')
        content = json_bytes(_json_document(dialect, selected))
    return [RenderedArtifact(path, content, 'mcp-fragment' if target.config_scope == 'user' else 'mcp')]
