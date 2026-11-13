"""Offline inventory, staging, diagnostics and benchmark commands."""
import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from .adapters import render_mcp, stdio_inventory
from .artifacts import RenderedArtifact, validate_artifacts, write_artifacts
from .compare import compare_runs
from .config import WeaverError, load_json, safe_name, validate_catalog, validate_profile
from .coverage import coverage_report, resolve_product_alias
from .integrations import (
    IntegrationSpec,
    integration_inventory,
    inventory_text,
    load_integrations,
    load_operating_systems,
)
from .models import OPENROUTER_BATCH_ENDPOINTS
from .platforms import describe_workflow, detect_host_os
from .report import as_markdown, read_runs, summarize
from .resources import resource_root


class SafeParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault('allow_abbrev', False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, 'ERROR: Invalid arguments; use --help for supported options. Credential values are not accepted.\n')


def _parser() -> argparse.ArgumentParser:
    parser = SafeParser(prog='agentweaver')
    subs = parser.add_subparsers(dest='cmd', required=True)
    check = subs.add_parser('validate')
    check.add_argument('--root', type=Path)
    inventory = subs.add_parser('inventory')
    inventory.add_argument('--root', type=Path)
    inventory.add_argument('--format', choices=['json', 'text'], default='text')
    coverage = subs.add_parser('coverage')
    coverage.add_argument('--root', type=Path)
    coverage.add_argument('--format', choices=['json', 'text'], default='text')
    coverage.add_argument('--strict', action='store_true')
    models = subs.add_parser('models')
    models.add_argument('--root', type=Path)
    models.add_argument('--provider')
    models.add_argument('--agent')
    models.add_argument('--surface')
    models.add_argument('--input', type=Path)
    models.add_argument('--format', choices=['json', 'text'], default='json')
    computer = subs.add_parser('computer-models', help='Inspect a saved Perplexity Computer models_list response')
    computer.add_argument('--input', type=Path, required=True)
    computer_selection = computer.add_mutually_exclusive_group()
    computer_selection.add_argument('--model')
    computer_selection.add_argument('--mode')
    computer.add_argument('--reasoning-effort')
    kimi_models = subs.add_parser('kimi-models', help='Inspect a saved Kimi web configured-model response')
    kimi_models.add_argument('--input', type=Path, required=True)
    kimi_models.add_argument('--model', help='Configured Kimi model alias')
    kimi_models.add_argument('--reasoning-effort')
    codex_models = subs.add_parser('codex-models', help='Inspect saved Codex app-server model/list replies')
    codex_models.add_argument('--input', type=Path, required=True)
    codex_models.add_argument('--model')
    codex_models.add_argument('--reasoning-effort')
    copilot_models = subs.add_parser('copilot-models', help='Inspect a saved Copilot SDK listModels() array')
    copilot_models.add_argument('--input', type=Path, required=True)
    copilot_models.add_argument('--model', action='append', help='Model ID to check; repeat to check fallbacks')
    copilot_models.add_argument('--reasoning-effort')
    claude_models = subs.add_parser('claude-models', help='Inspect a saved Claude Code Agent SDK ModelInfo array')
    claude_models.add_argument('--input', type=Path, required=True)
    claude_models.add_argument('--model')
    claude_models.add_argument('--reasoning-effort')
    factory_models = subs.add_parser('factory-models', help='Inspect a saved Factory Droid SDK listModels() array')
    factory_models.add_argument('--input', type=Path, required=True)
    factory_models.add_argument('--model')
    factory_models.add_argument('--reasoning-effort')
    qoder_models = subs.add_parser('qoder-models', help='Inspect a saved Qoder Agent SDK getAvailableModels() array')
    qoder_models.add_argument('--input', type=Path, required=True)
    qoder_models.add_argument('--model')
    qoder_models.add_argument('--reasoning-effort')
    qoder_models.add_argument('--context-size', type=int)
    qwen_models = subs.add_parser('qwen-models', help='Inspect a saved Qwen Code daemon workspace providers response')
    qwen_models.add_argument('--input', type=Path, required=True)
    qwen_models.add_argument('--auth-type')
    qwen_models.add_argument('--model')
    qwen_models.add_argument('--reasoning-effort')
    agents = subs.add_parser('agents')
    agents.add_argument('action', nargs='?', choices=['inventory'], default='inventory')
    agents.add_argument('--root', type=Path)
    agents.add_argument('--agent')
    agents.add_argument('--surface')
    agents.add_argument('--format', choices=['json', 'text'], default='json')
    for name in ('render', 'bundle', 'provider', 'doctor', 'model', 'agent'):
        command = subs.add_parser(name)
        command.add_argument('--root', type=Path)
        if name == 'render':
            selectors = command.add_mutually_exclusive_group(required=True)
            selectors.add_argument('--agent')
            selectors.add_argument('--platform', help='Deprecated alias for --agent kilo|abacus')
        else:
            command.add_argument('--agent', required=True)
        command.add_argument('--surface')
        command.add_argument('--os')
        if name == 'doctor':
            command.add_argument('--format', choices=['json', 'text'], default='text')
            command.add_argument('--strict', action='store_true')
            continue
        command.add_argument('--output-dir', type=Path, default=Path('generated'))
        command.add_argument('--dry-run', action='store_true')
        command.add_argument('--force', action='store_true')
        if name in ('render', 'bundle'):
            command.add_argument('--profile', required=True)
        if name == 'render':
            command.add_argument('--enable', action='append', default=[])
            render_route = command.add_mutually_exclusive_group()
            render_route.add_argument('--opencode-v2', action='store_true',
                                 help='Stage selected servers as disabled OpenCode V2 project entries')
            render_route.add_argument('--copilot-portable', action='store_true',
                                      help='Stage portable MCP configuration for the VS Code Copilot harness')
        elif name == 'bundle':
            command.add_argument('--skill', action='append', default=[])
        elif name in ('provider', 'model'):
            command.add_argument('--provider', required=name == 'provider')
            command.add_argument('--model', required=True)
            command.add_argument('--catalog', type=Path)
            command.add_argument('--protocol')
            command.add_argument('--reasoning-effort')
            command.add_argument('--reasoning-budget', type=int)
            command.add_argument('--reasoning', choices=['on', 'off'])
            command.add_argument('--reasoning-mode')
            command.add_argument('--context-size', type=int)
            command.add_argument('--batch-endpoint', choices=OPENROUTER_BATCH_ENDPOINTS,
                                 help='Request body endpoint for OpenRouter batch staging')
            if name == 'model':
                host_catalog = command.add_mutually_exclusive_group()
                host_catalog.add_argument('--claude-catalog', type=Path, help='Saved Claude Code Agent SDK ModelInfo array')
                host_catalog.add_argument('--factory-catalog', type=Path, help='Saved Factory Droid SDK listModels() array')
                host_catalog.add_argument('--qwen-workspace-catalog', type=Path,
                                          help='Saved Qwen daemon GET /workspace/providers response')
                host_catalog.add_argument('--kimi-catalog', type=Path, help='Saved Kimi web GET /api/v1/models response')
                host_catalog.add_argument('--copilot-catalog', type=Path, help='Saved Copilot SDK listModels() JSON array')
                command.add_argument('--kimi-workspace-id', help='Registered local workspace for Kimi web session staging')
                command.add_argument('--kimi-agent-profile', help='Existing Kimi main-agent profile name')
                command.add_argument('--kimi-subagent-pool', action='store_true',
                                     help='Stage a Kimi subagent model pool instead of a web session request')
                command.add_argument('--kimi-subagent-alias', action='append', default=[],
                                     help='Add an alias to the Kimi subagent model pool')
                command.add_argument('--qwen-auth-type', help='Filter the saved Qwen workspace catalog by authentication type')
                command.add_argument('--zed-subagent', action='store_true',
                                     help='Stage the OpenRouter model as Zed Agent’s subagent default')
                command.add_argument('--kiro-model-defaults', action='store_true',
                                     help='Stage Kiro CLI V3 per-model reasoning settings')
                command.add_argument('--qoder-settings', action='store_true',
                                     help='Stage a Qoder CLI model settings fragment for manual merge')
                host_catalog.add_argument('--qoder-catalog', type=Path, help='Saved Qoder Agent SDK getAvailableModels() array')
        elif name == 'agent':
            command.add_argument('--name', required=True)
            command.add_argument('--description', required=True)
            command.add_argument('--instructions-file', type=Path, required=True)
            command.add_argument('--model')
            command.add_argument('--llm-profile', help='Existing OpenHands local LLM profile reference; no profile is read or created')
            command.add_argument('--qwen-auth-type', help='Built-in Qwen Code authentication type for a subagent model')
            command.add_argument('--provider')
            command.add_argument('--catalog', type=Path)
            command.add_argument('--codex-catalog', type=Path, help='Saved Codex app-server model/list reply or pages')
            command.add_argument('--claude-catalog', type=Path, help='Saved Claude Code Agent SDK ModelInfo array')
            command.add_argument('--copilot-catalog', type=Path, help='Saved Copilot SDK listModels() JSON array')
            command.add_argument('--factory-catalog', type=Path, help='Saved Factory Droid SDK listModels() array')
            command.add_argument('--qoder-catalog', type=Path, help='Saved Qoder Agent SDK getAvailableModels() array')
            command.add_argument('--copilot-fallback-model', action='append', help='Add a Copilot model in fallback order')
            command.add_argument('--copilot-model-policy', choices=['preferred', 'required'],
                                 help='Set the documented Copilot model policy for the selected surface')
            command.add_argument('--opencode-catalog', type=Path, help='Saved OpenCode V2 GET /api/model JSON response')
            command.add_argument('--kilo-catalog', type=Path, help='Saved Kilo GET /api/model JSON response')
            command.add_argument('--model-variant', help='Named variant from the saved OpenCode V2 or Kilo model list')
            command.add_argument('--allow-skill', action='append', help='Select a portable Skill for OpenCode V2, Copilot, or Cline CLI/Desktop')
            command.add_argument('--hermes-profile', action='store_true', help='Stage a Hermes profile distribution')
            command.add_argument('--hermes-profile-route', action='store_true',
                                 help='Stage a separate Hermes parent delegation fragment for this profile')
            command.add_argument('--profile-skill', action='append', help='Bundle a portable Skill in a staged Hermes profile')
            command.add_argument('--mcp-profile', help='Weaver profile ID that allowlists Hermes MCP servers')
            command.add_argument('--profile-mcp', action='append', help='Bundle an allowlisted MCP server, disabled')
            command.add_argument('--profile-context-size', type=int, help='Explicit context length for a Hermes LM Studio profile')
            command.add_argument('--reasoning-effort')
    report = subs.add_parser('report')
    report.add_argument('--input', type=Path, required=True)
    report.add_argument('--format', choices=['json', 'markdown'], default='markdown')
    report.add_argument('--output', type=Path)
    compare = subs.add_parser('compare')
    compare.add_argument('--input', type=Path, required=True)
    compare.add_argument('--agent', required=True)
    compare.add_argument('--source', choices=['measured', 'synthetic'], default='measured')
    compare.add_argument('--baseline', default='baseline')
    compare.add_argument('--candidate', default='optimized')
    compare.add_argument('--output', type=Path)
    return parser


def _paths(args: argparse.Namespace) -> list[str] | None:
    if args.cmd == 'validate':
        return None
    paths = ['catalogs/integrations.json', 'catalogs/operating-systems.json']
    if args.cmd != 'inventory':
        paths.append('catalogs/requested-coverage.json')
    if args.cmd in ('coverage', 'models', 'model', 'provider', 'agents', 'agent'):
        paths.append('catalogs/model-providers.json')
    if args.cmd in ('coverage', 'models'):
        paths.append('catalogs/model-hosts.json')
    if args.cmd in ('coverage', 'agents', 'agent'):
        paths.append('catalogs/custom-agents.json')
    mcp_profile = getattr(args, 'mcp_profile', None)
    if args.cmd in ('render', 'bundle') or mcp_profile is not None:
        profile_id = args.profile if args.cmd in ('render', 'bundle') else mcp_profile
        if not safe_name(profile_id):
            raise WeaverError('Invalid profile name; use an allowlisted profile ID')
        paths.append('catalogs/mcp-servers.json')
        if args.root is None:
            paths.append(f'profiles/{profile_id}.json')
    if args.cmd == 'bundle' or (args.cmd == 'agent' and (args.allow_skill or args.profile_skill)):
        paths.append('.agents/skills')
    return paths


def _selection(args: argparse.Namespace, root: Path) -> tuple[IntegrationSpec, str, str, str]:
    agent_id = getattr(args, 'agent', None) or getattr(args, 'platform', None)
    if getattr(args, 'platform', None) not in (None, 'kilo', 'abacus'):
        raise WeaverError('Legacy --platform accepts kilo or abacus; use --agent for other integrations')
    if not isinstance(agent_id, str):
        raise WeaverError('Invalid product selection; inspect inventory or coverage')
    integrations = load_integrations(root)
    if agent_id not in integrations:
        agent_id = resolve_product_alias(root, agent_id)
    spec = integrations[agent_id]
    surface = args.surface or spec.default_surface
    if surface not in spec.surface_ids():
        raise WeaverError('Unsupported surface; inspect inventory for documented routes')
    host = detect_host_os()
    if args.os is None and host == 'unknown':
        raise WeaverError('Host OS could not be detected; specify --os for artifact handoff')
    target = args.os or host.split('-', 1)[0]
    if target not in load_operating_systems(root):
        raise WeaverError('Unknown target OS; specify --os using an inventory OS ID')
    return spec, surface, target, host


def _handoff(spec: IntegrationSpec, surface: str, target_os: str, host_os: str,
             *, mcp: bool = False, label: str | None = None, opencode_v2: bool = False,
             copilot_portable: bool = False) -> RenderedArtifact:
    target = spec.surface(surface)
    workflow = describe_workflow(spec, target_os, host_os, surface_id=surface)
    sources = list(dict.fromkeys(source for evidence in target.capabilities.values() for source in evidence.sources))
    lines = [f'# {spec.name}: staged {surface} handoff', '',
             'Review these artifacts before any manual import. Nothing has been installed, applied or launched.',
             'No credentials were read or written. Ambient/global vendor configuration was not inspected.',
             f'Target OS: {target_os}. Generating host: {host_os}.',
             'Documentation and fixture tests are not installed-client or native-device verification.', '']
    if mcp:
        if (spec.id, surface) in {('cline', 'cli'), ('cline', 'desktop'), ('kiro', 'cli')}:
            lines.extend(['Every staged server is disabled. Review each transport and its tool access.',
                          'Merge selected entries into the MCP settings and explicitly enable approved servers.'])
        if target.config_scope == 'user':
            lines.extend(['User-scope fragment only: use the vendor settings interface to review and manually merge',
                          f'the selected entries at {target.documented_location or "the documented user settings location"}.',
                          'Do not copy this fragment into a project or replace the entire user configuration.'])
        elif target.config_scope == 'request':
            lines.extend(['API request fragment only: merge into the documented request after review.',
                          'No request was sent and no local server, connector, or MCP setting was enabled.'])
        elif target.config_scope == 'app':
            lines.extend(['Application-scope fragment: review and import entries through the vendor settings interface.',
                          f'Import location: {target.documented_location or "the documented application settings interface"}.',
                          'Preserve unrelated application settings; this fragment does not replace them.'])
            if target.native_path_hint is None:
                lines.append('No persistent settings file path is established for this import route.')
        else:
            path_hint = '.mcp.json' if copilot_portable else target.native_path_hint
            lines.extend([f'Project target after manual review: {path_hint}.',
                          'Preserve unrelated settings; review existing entries before merging.'])
        lines.extend(['Empty exports select no MCP servers; they do not disable existing global/project servers.',
                      'Vendor project trust/approval remains required; generated profiles do not bypass it.'])
        if spec.id == 'abacus':
            lines.append('Abacus user-level same-name MCP entries override project entries.')
        if spec.id == 'devin':
            lines.append('This is current Devin Local configuration, not a legacy Cascade configuration migration.')
        if (spec.id, surface) == ('claude-code', 'desktop'):
            lines.extend([
                'Use the staged .mcp.json in a local Code tab session and review project MCP approvals in the app.',
                'Review same-name servers in claude_desktop_config.json and user-scope stdio servers in ~/.claude.json. '
                'Desktop can prefer those definitions over the project entry.',
            ])
        if spec.id == 'copilot':
            if copilot_portable or surface in {'cli', 'desktop'}:
                destination = 'Select the Copilot harness in VS Code. ' if surface == 'vscode' else ''
                lines.extend([
                    destination + 'Review each server before manually merging it into the workspace-root .mcp.json.',
                    'Imported entries may start in the destination. Each entry exposes all its tools by default; review and narrow tools before import.',
                ])
                if surface == 'desktop':
                    lines.append('Use a local repository session in the GitHub Copilot app and review server availability under Customize > MCP.')
            else:
                lines.append('This export uses editor-managed MCP configuration. Select --copilot-portable for the native Copilot workspace format.')
        if opencode_v2:
            lines.extend(['OpenCode V2 entries are staged under mcp.servers with disabled: true.',
                          'Merge only after review. OpenCode V2 replaces same-name server entries as a whole.',
                          'Enable each desired server explicitly in OpenCode after reviewing its URL or launch arguments.',
                          'V2 configuration: https://opencode.ai/docs/mcp-servers/'])
    lines.extend(['', f'Workflow: {workflow["workflow"]["mode"]}.',
                  'Pending native runtime evidence: record product/surface/version, exact OS variant and artifact hashes.'])
    if target_os == 'ios':
        lines.append('iOS: artifact/remote handoff only; use a separate supported host and a documented remote interface. No on-device CLI is provided.')
    elif target_os == 'android':
        lines.append('Android: conditional compatible Python/Termux core workflow, otherwise remote handoff. Agent-native Termux support is independent; consult the cited vendor docs.')
    elif target_os in ('bsd', 'solaris'):
        lines.append('Core CLI eligibility requires Python 3.11+ and actual host validation; no native vendor-agent claim is made.')
    if host_os.split('-', 1)[0] != target_os:
        lines.append('Host and target differ: these staged files do not establish target execution.')
    lines.extend(['', 'Profile retrieval and production-write advice is advisory, not an enforced permission system.',
                  'Do not enable remote listeners, open ports, install agents or weaken authentication to use this bundle.',
                  '', 'Documentation sources:', *[f'- {source}' for source in sources], ''])
    name = label or ('MCP' if mcp else 'OS')
    return RenderedArtifact(PurePosixPath(spec.id, f'{name}-HANDOFF.md'),
                            '\n'.join(lines).encode('utf-8'), 'handoff')


def _emit(args: argparse.Namespace, artifacts: list[RenderedArtifact]) -> None:
    if args.dry_run:
        validate_artifacts(artifacts)
        data = {'schema_version': 1, 'artifacts': [
            {'path': item.relative_path.as_posix(), 'kind': item.kind, 'content': item.content.decode('utf-8')}
            for item in artifacts
        ]}
        print(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        for path in write_artifacts(args.output_dir, artifacts, force=args.force):
            print(path)


def _benchmark(args: argparse.Namespace) -> int:
    if args.cmd == 'report':
        result = summarize(read_runs(args.input))
        body = json.dumps(result, indent=2) + '\n' if args.format == 'json' else as_markdown(result)
    else:
        result = compare_runs(read_runs(args.input), agent=args.agent, source=args.source,
                              baseline=args.baseline, candidate=args.candidate)
        body = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding='utf-8')
        print('Generated', args.output)
    else:
        print(body, end='')
    return 0


def _model_snapshot(args: argparse.Namespace, root: Path) -> dict[str, Any] | None:
    from .models import read_model_snapshot

    path = getattr(args, 'catalog', None)
    if path is None:
        return None
    provider = getattr(args, 'provider', None)
    if provider is None:
        raise WeaverError('A model catalog requires an explicit provider')
    return read_model_snapshot(path, provider, root)


def _reasoning(args: argparse.Namespace) -> bool | None:
    selected = getattr(args, 'reasoning', None)
    return None if selected is None else selected == 'on'


def _extended_inventory(args: argparse.Namespace, root: Path) -> int:
    from .agents import agent_inventory
    from .models import load_model_providers, model_inventory, read_model_snapshot

    if args.cmd == 'coverage':
        data = coverage_report(root)
        data['model_catalogs'] = model_inventory(root)
        data['custom_agents'] = agent_inventory(root)
        data['mcp_transports'] = {'stdio': {
            'documented_adapters': stdio_inventory(),
            'runtime_verified': False,
            'unestablished_file_exports': ['jetbrains-air/desktop', 'jetbrains-air/ide'],
            'remote_only_requests': ['perplexity/agent-api', 'lm-studio/api'],
        }}
        summary = data['summary']
        data['full_requested_support_verified'] = (
            summary['missing_documentation_capabilities'] == 0
            and summary['pending_runtime_capabilities'] == 0
            and not any(summary['capability_states'].get(state, 0)
                        for state in ('unknown', 'manual', 'unsupported'))
            and data['model_catalogs']['catalog_completeness'] == 'complete'
        )
        if args.format == 'text':
            print('Requested scope mappings:', summary['mapped_items'], '/', summary['requested_items'])
            print('Referenced product surfaces:', summary['referenced_surfaces'])
            print('Capability states:', json.dumps(summary['capability_states'], sort_keys=True))
            print('Pending runtime capability records:', summary['pending_runtime_capabilities'])
            print('Scoped runtime checks:', summary['runtime_observation_checks'],
                  'across', summary['surfaces_with_runtime_observations'], 'surfaces')
            print('Full requested support verified:', data['full_requested_support_verified'])
            print('Model inventory completeness: unknown; catalog visibility is not successful inference.')
            for item in data['requested_items']:
                targets = ', '.join(f"{target['product_id']}/{target['surface_id']}" for target in item['targets'])
                print(f"{item['label']}: {targets}")
        else:
            print(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False))
        return 1 if args.strict and not data['full_requested_support_verified'] else 0
    if args.cmd == 'models':
        if args.surface is not None and args.agent is None:
            raise WeaverError('Select a product before selecting a model-control surface')
        if args.input is not None:
            if args.provider is None:
                raise WeaverError('Model snapshot import requires an explicit provider')
            if args.agent is not None:
                raise WeaverError('Inspect host controls separately from importing a provider snapshot')
            data = read_model_snapshot(args.input, args.provider, root)
        else:
            data = model_inventory(root)
            if args.provider is not None:
                providers = load_model_providers(root)
                if args.provider not in providers:
                    raise WeaverError('Unknown model provider; inspect models inventory')
                data['providers'] = [providers[args.provider]]
            if args.agent is not None:
                product = resolve_product_alias(root, args.agent)
                if args.surface is not None:
                    load_integrations(root)[product].surface(args.surface)
                data['hosts'] = [entry for entry in data['hosts']
                                 if entry['product_id'] == product
                                 and (args.surface is None or entry['surface_id'] == args.surface)]
    else:
        data = agent_inventory(root)
        if args.surface is not None and args.agent is None:
            raise WeaverError('Select a product before selecting an agent-definition surface')
        if args.agent is not None:
            product = resolve_product_alias(root, args.agent)
            if args.surface is not None:
                load_integrations(root)[product].surface(args.surface)
            data['definitions'] = [entry for entry in data['definitions']
                                   if entry['product_id'] == product
                                   and (args.surface is None or entry['surface_id'] == args.surface)]
    # Both forms retain provenance and unknown states; text is pretty JSON for these inventories.
    print(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False))
    return 0


def _run(args: argparse.Namespace, root: Path) -> int:
    if args.cmd in ('coverage', 'models', 'agents'):
        return _extended_inventory(args, root)
    if args.cmd == 'inventory':
        if args.format == 'json':
            print(json.dumps(integration_inventory(root), indent=2))
        else:
            print(inventory_text(root), end='')
        return 0
    if args.cmd == 'validate':
        from .agents import agent_inventory
        from .models import load_model_hosts, load_model_providers
        from .skills import validate_skills

        catalog = load_json(root / 'catalogs/mcp-servers.json')
        errors = validate_catalog(catalog)
        load_operating_systems(root)
        coverage_report(root)
        load_model_providers(root)
        load_model_hosts(root)
        agent_inventory(root)
        profiles = sorted((root / 'profiles').glob('*.json'))
        if not profiles:
            errors.append('No profiles found')
        for path in profiles:
            errors.extend(validate_profile(load_json(path), catalog))
        errors.extend(validate_skills(root))
        if errors:
            for error in errors:
                print('ERROR:', error, file=sys.stderr)
            return 1
        print('Configuration, registries, requested coverage and portable skill validation passed')
        return 0
    spec, surface, target_os, host_os = _selection(args, root)
    if args.cmd == 'model' and args.qwen_auth_type is not None and args.qwen_workspace_catalog is None:
        raise WeaverError('Qwen model authentication filtering requires --qwen-workspace-catalog')
    if (args.cmd == 'model' and args.kimi_catalog is None
            and (args.kimi_subagent_pool or args.kimi_subagent_alias
                 or args.kimi_workspace_id is not None or args.kimi_agent_profile is not None)):
        raise WeaverError('Kimi session and subagent model options require --kimi-catalog')
    if args.cmd == 'doctor':
        diagnosis = describe_workflow(spec, target_os, host_os, surface_id=surface)
        if args.format == 'json':
            print(json.dumps(diagnosis, indent=2))
        else:
            print(f'{spec.id}/{surface}: target={target_os}, host={host_os}')
            print(f'Core runtime: {diagnosis["core_runtime"]["status"]}; adapter: {diagnosis["adapter"]["status"]}')
            print(f'Native vendor: {diagnosis["native_vendor"]["status"]}; workflow: {diagnosis["workflow"]["mode"]}')
            print('Native installed-version evidence is separate; ambient settings were not inspected.')
        return 1 if args.strict and not diagnosis['strict_ready'] else 0
    if args.cmd in ('render', 'bundle'):
        profile = load_json(root / 'profiles' / f'{args.profile}.json')
        if args.cmd == 'render':
            catalog = load_json(root / 'catalogs/mcp-servers.json')
            artifacts = render_mcp(spec, surface, profile, catalog, args.enable,
                                   opencode_v2=args.opencode_v2, copilot_portable=args.copilot_portable)
        else:
            from .skills import render_instruction_bundle

            catalog = load_json(root / 'catalogs/mcp-servers.json')
            errors = validate_catalog(catalog) + validate_profile(profile, catalog)
            if errors:
                raise WeaverError('; '.join(errors))
            artifacts = render_instruction_bundle(spec, surface, profile, args.skill, root)
    elif args.cmd == 'model' and args.kimi_catalog is not None:
        from .kimi_models import render_kimi_session, render_kimi_subagent_pool

        if (spec.id, surface) != ('kimi-code', 'cli'):
            raise WeaverError('A Kimi web model catalog requires the Kimi Code CLI target')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode',
             'context_size', 'batch_endpoint')
        ):
            raise WeaverError('Kimi model staging uses configured aliases and an optional thinking effort')
        if args.kimi_subagent_pool:
            if args.kimi_workspace_id is not None or args.kimi_agent_profile is not None:
                raise WeaverError('A Kimi subagent pool cannot include web session options')
            artifacts = render_kimi_subagent_pool(args.kimi_catalog, args.model, args.reasoning_effort,
                                                 alternatives=args.kimi_subagent_alias)
        else:
            if args.kimi_subagent_alias:
                raise WeaverError('--kimi-subagent-alias requires --kimi-subagent-pool')
            if args.kimi_workspace_id is None:
                raise WeaverError('Kimi web session staging requires --kimi-workspace-id')
            artifacts = render_kimi_session(args.kimi_catalog, args.model, args.reasoning_effort,
                                           workspace_id=args.kimi_workspace_id, profile=args.kimi_agent_profile)
    elif args.cmd == 'model' and args.qwen_workspace_catalog is not None:
        from .qwen_models import render_qwen_startup

        if spec.id != 'qwen-code' or surface not in {'cli', 'desktop', 'vscode'}:
            raise WeaverError('A Qwen workspace catalog requires a Qwen Code workspace target')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode',
             'context_size', 'batch_endpoint')
        ):
            raise WeaverError('Qwen daemon startup uses a workspace model route and optional reasoning selection')
        artifacts = render_qwen_startup(args.qwen_workspace_catalog, args.model, args.reasoning_effort,
                                       auth_type=args.qwen_auth_type)
    elif args.cmd == 'model' and args.claude_catalog is not None:
        from .claude_models import render_claude_model

        if spec.id != 'claude-code' or surface not in {'cli', 'desktop', 'vscode'}:
            raise WeaverError('A Claude Code model catalog requires a local Claude Code target')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode',
             'context_size', 'batch_endpoint')
        ):
            raise WeaverError('Claude Code project model staging uses a saved selector and optional effort')
        artifacts = render_claude_model(args.claude_catalog, surface, args.model, args.reasoning_effort)
    elif args.cmd == 'model' and args.factory_catalog is not None:
        from .factory_models import render_factory_exec

        if (spec.id, surface) != ('factory', 'cli'):
            raise WeaverError('A Factory Droid SDK catalog requires the Factory CLI target')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode',
             'context_size', 'batch_endpoint', 'qoder_catalog')
        ):
            raise WeaverError('A Factory Droid SDK catalog uses a managed model ID and optional reasoning effort')
        artifacts = render_factory_exec(args.factory_catalog, args.model, args.reasoning_effort)
    elif args.cmd == 'model' and args.copilot_catalog is not None:
        from .copilot_models import render_copilot_cli_model

        if (spec.id, surface) != ('copilot', 'cli'):
            raise WeaverError('Copilot repository model settings require the Copilot CLI target')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode',
             'context_size', 'batch_endpoint')
        ):
            raise WeaverError('Copilot repository model settings use an explicit model ID and optional effort')
        artifacts = render_copilot_cli_model(args.copilot_catalog, args.model, args.reasoning_effort)
    elif args.cmd == 'model' and (spec.id, surface) == ('kiro', 'cli'):
        from .models import render_kiro_cli_model

        if args.zed_subagent or args.qoder_settings or any(getattr(args, option) is not None for option in
                                   ('provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning',
                                    'reasoning_mode', 'context_size', 'batch_endpoint', 'qoder_catalog')):
            raise WeaverError('Kiro CLI model staging uses Kiro model IDs and an optional reasoning effort')
        artifacts = render_kiro_cli_model(args.model, args.reasoning_effort, persistent=args.kiro_model_defaults)
    elif args.cmd == 'model' and (spec.id, surface) == ('qoder', 'cli'):
        from .qoder_models import render_qoder_cli_model

        if args.zed_subagent or args.kiro_model_defaults or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode', 'batch_endpoint')
        ):
            raise WeaverError('Qoder CLI model staging uses a Qoder model ID, optional effort and context window')
        artifacts = render_qoder_cli_model(args.model, args.reasoning_effort, args.context_size,
                                           persistent=args.qoder_settings, catalog=args.qoder_catalog)
    elif args.cmd in ('provider', 'model'):
        from .providers import render_provider_setup

        if getattr(args, 'qoder_settings', False) or getattr(args, 'qoder_catalog', None) is not None:
            raise WeaverError('Qoder model settings require the Qoder CLI surface')
        if args.provider is None:
            raise WeaverError('This model route requires an explicit provider')
        if getattr(args, 'kiro_model_defaults', False):
            raise WeaverError('Kiro per-model defaults require the Kiro CLI surface')
        extended = args.cmd == 'model' or args.provider != 'openrouter' or any(
            getattr(args, key) is not None for key in
            ('catalog', 'protocol', 'reasoning_effort', 'reasoning_budget', 'reasoning', 'reasoning_mode',
             'context_size', 'batch_endpoint')
        )
        if extended:
            from .models import render_model_setup

            artifacts = render_model_setup(spec, surface, root=root, provider_id=args.provider, model_id=args.model,
                                           snapshot=_model_snapshot(args, root), effort=args.reasoning_effort,
                                           budget_tokens=args.reasoning_budget, reasoning_enabled=_reasoning(args),
                                           protocol=args.protocol, mode=args.reasoning_mode, context_size=args.context_size,
                                           batch_endpoint=args.batch_endpoint,
                                           zed_subagent=getattr(args, 'zed_subagent', False))
        else:
            gateway = load_integrations(root)['openrouter']
            metadata = gateway.surface(gateway.default_surface).provider
            artifacts = render_provider_setup(spec, surface, provider_id=args.provider, model_id=args.model,
                                              provider_metadata=metadata)
    elif args.cmd == 'agent':
        from .agents import render_agent_definition

        if args.llm_profile is not None and any(value is not None for value in
                                               (args.model, args.provider, args.catalog, args.reasoning_effort)):
            raise WeaverError('A local LLM profile cannot be combined with model, provider, catalog or effort overrides')
        if args.catalog is not None and args.provider is None:
            raise WeaverError('A model catalog requires an explicit provider')
        artifacts = render_agent_definition(
            spec, surface, root=root, name=args.name, description=args.description,
            instructions=args.instructions_file.read_text(encoding='utf-8'), model_id=args.model,
            effort=args.reasoning_effort, provider_id=args.provider, snapshot=_model_snapshot(args, root),
            llm_profile=args.llm_profile, codex_catalog=args.codex_catalog,
            copilot_catalog=args.copilot_catalog, copilot_fallbacks=args.copilot_fallback_model,
            copilot_model_policy=args.copilot_model_policy,
            opencode_catalog=args.opencode_catalog, model_variant=args.model_variant,
            kilo_catalog=args.kilo_catalog,
            factory_catalog=args.factory_catalog,
            qoder_catalog=args.qoder_catalog, claude_catalog=args.claude_catalog,
            qwen_auth_type=args.qwen_auth_type,
            allowed_skills=args.allow_skill,
            hermes_profile=args.hermes_profile, profile_skills=args.profile_skill,
            hermes_profile_route=args.hermes_profile_route,
            mcp_profile=args.mcp_profile, profile_mcp=args.profile_mcp,
            profile_context_size=args.profile_context_size,
        )
    else:
        raise WeaverError('Unsupported command')
    artifacts.append(_handoff(spec, surface, target_os, host_os, mcp=args.cmd == 'render',
                              label={'provider': 'PROVIDER-OS', 'model': 'MODEL-OS', 'agent': 'AGENT-OS'}.get(args.cmd),
                              opencode_v2=args.cmd == 'render' and args.opencode_v2,
                              copilot_portable=args.cmd == 'render' and args.copilot_portable))
    _emit(args, artifacts)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd in ('report', 'compare'):
            return _benchmark(args)
        if args.cmd == 'computer-models':
            from .computer_models import inspect_computer_models

            result = inspect_computer_models(args.input, model=args.model, mode=args.mode,
                                             effort=args.reasoning_effort)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'kimi-models':
            from .kimi_models import inspect_kimi_models

            result = inspect_kimi_models(args.input, model=args.model, effort=args.reasoning_effort)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'codex-models':
            from .codex_models import inspect_codex_models

            result = inspect_codex_models(args.input, model=args.model, effort=args.reasoning_effort)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'copilot-models':
            from .copilot_models import inspect_copilot_models

            result = inspect_copilot_models(args.input, models=args.model, effort=args.reasoning_effort)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'claude-models':
            from .claude_models import inspect_claude_models

            result = inspect_claude_models(args.input, model=args.model, effort=args.reasoning_effort)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'factory-models':
            from .factory_models import inspect_factory_models

            result = inspect_factory_models(args.input, model=args.model, effort=args.reasoning_effort)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'qoder-models':
            from .qoder_models import inspect_qoder_models

            result = inspect_qoder_models(args.input, model=args.model, effort=args.reasoning_effort,
                                          context_size=args.context_size)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'qwen-models':
            from .qwen_models import inspect_qwen_models

            result = inspect_qwen_models(args.input, auth_type=args.auth_type, model=args.model,
                                         effort=args.reasoning_effort)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        with resource_root(args.root, paths=_paths(args)) as root:
            return _run(args, root)
    except WeaverError as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print('ERROR: Unable to process the requested offline operation', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
