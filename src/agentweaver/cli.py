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
from .coverage import coverage_report, load_requested_scope, resolve_product_alias
from .integrations import (
    IntegrationSpec,
    integration_inventory,
    inventory_text,
    load_integrations,
    load_operating_systems,
)
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
        elif name == 'bundle':
            command.add_argument('--skill', action='append', default=[])
        elif name in ('provider', 'model'):
            command.add_argument('--provider', required=True)
            command.add_argument('--model', required=True)
            command.add_argument('--catalog', type=Path)
            command.add_argument('--protocol')
            command.add_argument('--reasoning-effort')
            command.add_argument('--reasoning-budget', type=int)
            command.add_argument('--reasoning', choices=['on', 'off'])
            command.add_argument('--reasoning-mode')
            command.add_argument('--context-size', type=int)
        elif name == 'agent':
            command.add_argument('--name', required=True)
            command.add_argument('--description', required=True)
            command.add_argument('--instructions-file', type=Path, required=True)
            command.add_argument('--model')
            command.add_argument('--llm-profile', help='Existing OpenHands local LLM profile reference; no profile is read or created')
            command.add_argument('--provider')
            command.add_argument('--catalog', type=Path)
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
    if args.cmd in ('render', 'bundle'):
        if not safe_name(args.profile):
            raise WeaverError('Invalid profile name; use an allowlisted profile ID')
        paths.append('catalogs/mcp-servers.json')
        if args.root is None:
            paths.append(f'profiles/{args.profile}.json')
    if args.cmd == 'bundle':
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
             *, mcp: bool = False, label: str | None = None) -> RenderedArtifact:
    target = spec.surface(surface)
    workflow = describe_workflow(spec, target_os, host_os, surface_id=surface)
    sources = list(dict.fromkeys(source for evidence in target.capabilities.values() for source in evidence.sources))
    lines = [f'# {spec.name}: staged {surface} handoff', '',
             'Review these artifacts before any manual import. Nothing has been installed, applied or launched.',
             'No credentials were read or written. Ambient/global vendor configuration was not inspected.',
             f'Target OS: {target_os}. Generating host: {host_os}.',
             'Documentation and fixture tests are not installed-client or native-device verification.', '']
    if mcp:
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
            lines.extend([f'Project target after manual review: {target.native_path_hint}.',
                          'Preserve unrelated settings; review existing entries before merging.'])
        lines.extend(['Empty exports select no MCP servers; they do not disable existing global/project servers.',
                      'Vendor project trust/approval remains required; generated profiles do not bypass it.'])
        if spec.id == 'abacus':
            lines.append('Abacus user-level same-name MCP entries override project entries.')
        if spec.id == 'devin':
            lines.append('This is current Devin Local configuration, not a legacy Cascade configuration migration.')
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
        load_integrations(root)
        load_operating_systems(root)
        load_requested_scope(root)
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
            artifacts = render_mcp(spec, surface, profile, catalog, args.enable)
        else:
            from .skills import render_instruction_bundle

            catalog = load_json(root / 'catalogs/mcp-servers.json')
            errors = validate_catalog(catalog) + validate_profile(profile, catalog)
            if errors:
                raise WeaverError('; '.join(errors))
            artifacts = render_instruction_bundle(spec, surface, profile, args.skill, root)
    elif args.cmd in ('provider', 'model'):
        from .providers import render_provider_setup

        extended = args.cmd == 'model' or args.provider != 'openrouter' or any(
            getattr(args, key) is not None for key in
            ('catalog', 'protocol', 'reasoning_effort', 'reasoning_budget', 'reasoning', 'reasoning_mode', 'context_size')
        )
        if extended:
            from .models import render_model_setup

            artifacts = render_model_setup(spec, surface, root=root, provider_id=args.provider, model_id=args.model,
                                           snapshot=_model_snapshot(args, root), effort=args.reasoning_effort,
                                           budget_tokens=args.reasoning_budget, reasoning_enabled=_reasoning(args),
                                           protocol=args.protocol, mode=args.reasoning_mode, context_size=args.context_size)
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
            llm_profile=args.llm_profile,
        )
    else:
        raise WeaverError('Unsupported command')
    artifacts.append(_handoff(spec, surface, target_os, host_os, mcp=args.cmd == 'render',
                              label={'provider': 'PROVIDER-OS', 'model': 'MODEL-OS', 'agent': 'AGENT-OS'}.get(args.cmd)))
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
