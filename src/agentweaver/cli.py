"""Offline inventory, staging, diagnostics and benchmark commands."""
import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from .adapters import render_mcp
from .artifacts import RenderedArtifact, validate_artifacts, write_artifacts
from .compare import compare_runs
from .config import WeaverError, load_json, safe_name, validate_catalog, validate_profile
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
    for name in ('render', 'bundle', 'provider', 'doctor'):
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
        else:
            command.add_argument('--provider', required=True)
            command.add_argument('--model', required=True)
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
    if not safe_name(agent_id):
        raise WeaverError('Invalid agent ID; inspect inventory')
    integrations = load_integrations(root)
    if agent_id not in integrations:
        raise WeaverError('Unknown integration; inspect inventory')
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
        body = json.dumps(result, indent=2) + '\\n' if args.format == 'json' else as_markdown(result)
    else:
        result = compare_runs(read_runs(args.input), agent=args.agent, source=args.source,
                              baseline=args.baseline, candidate=args.candidate)
        body = json.dumps(result, indent=2) + '\\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding='utf-8')
        print('Generated', args.output)
    else:
        print(body, end='')
    return 0


def _run(args: argparse.Namespace, root: Path) -> int:
    if args.cmd == 'inventory':
        if args.format == 'json':
            print(json.dumps(integration_inventory(root), indent=2))
        else:
            print(inventory_text(root), end='')
        return 0
    if args.cmd == 'validate':
        from .skills import validate_skills

        catalog = load_json(root / 'catalogs/mcp-servers.json')
        errors = validate_catalog(catalog)
        load_integrations(root)
        load_operating_systems(root)
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
        print('Configuration, registry and portable skill validation passed')
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
    elif args.cmd == 'provider':
        from .providers import render_provider_setup

        gateway = load_integrations(root)['openrouter']
        metadata = gateway.surface(gateway.default_surface).provider
        artifacts = render_provider_setup(spec, surface, provider_id=args.provider, model_id=args.model,
                                          provider_metadata=metadata)
    else:
        raise WeaverError('Unsupported command')
    artifacts.append(_handoff(spec, surface, target_os, host_os, mcp=args.cmd == 'render',
                              label='PROVIDER-OS' if args.cmd == 'provider' else None))
    _emit(args, artifacts)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd in ('report', 'compare'):
            return _benchmark(args)
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
