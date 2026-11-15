"""Offline inventory, staging, diagnostics and benchmark commands."""
import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from .adapters import json_bytes, render_mcp, stdio_inventory
from .artifacts import RenderedArtifact, validate_artifacts, write_artifacts
from .bionic_models import BIONIC_MODEL_SOURCES
from .codex_models import CODEX_REASONING_SUMMARIES, CODEX_VERBOSITIES
from .compare import compare_runs
from .config import WeaverError, load_json, safe_name, select_servers, validate_catalog, validate_profile
from .coverage import coverage_report, resolve_product_alias
from .integrations import (
    IntegrationSpec,
    integration_inventory,
    inventory_text,
    load_integrations,
    load_operating_systems,
)
from .models import OPENROUTER_BATCH_ENDPOINTS, PERPLEXITY_SERVICE_TIERS, ZED_NATIVE_PROTOCOLS, ZED_SPEEDS
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


def _perplexity_mcp_options(command: argparse.ArgumentParser) -> None:
    command.add_argument('--perplexity-allow-tool', action='append', metavar='SERVER:TOOL',
                         help='Allow an exact tool on a selected Perplexity Agent API MCP server')
    command.add_argument('--perplexity-defer-tools', action='append', metavar='SERVER',
                         help='Defer tool schema loading on a selected Perplexity Agent API MCP server')


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
    model_input = models.add_mutually_exclusive_group()
    model_input.add_argument('--input', type=Path)
    model_input.add_argument('--openrouter-provider-endpoints', type=Path,
                             help='Inspect a saved OpenRouter model-specific provider endpoint listing')
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
    cursor_models = subs.add_parser('cursor-models', help='Inspect a saved Cursor SDK models.list() array')
    cursor_models.add_argument('--input', type=Path, required=True)
    cursor_models.add_argument('--model')
    cursor_models.add_argument('--reasoning-effort')
    cursor_models.add_argument('--cursor-param', action='append', help='Select a parameter as ID=VALUE')
    cursor_models.add_argument('--cursor-variant', metavar='NAME', help='Select an exact Cursor preset display name')
    copilot_models = subs.add_parser('copilot-models', help='Inspect a saved Copilot SDK listModels() array')
    copilot_models.add_argument('--input', type=Path, required=True)
    copilot_models.add_argument('--model', action='append', help='Model ID to check; repeat to check fallbacks')
    copilot_models.add_argument('--reasoning-effort')
    devin_models = subs.add_parser('devin-models', help='Inspect a saved Devin Desktop List Models response')
    devin_models.add_argument('--input', type=Path, required=True)
    devin_models.add_argument('--model', help='Exact model UID to validate')
    bionic_models = subs.add_parser('bionic-models', help='Inspect saved Bionic model picker options')
    bionic_models.add_argument('--input', type=Path, required=True)
    bionic_models.add_argument('--model', help='Exact Bionic picker model ID')
    bionic_models.add_argument('--source', choices=BIONIC_MODEL_SOURCES)
    bionic_models.add_argument('--reasoning-effort')
    devin_airgap = subs.add_parser('devin-airgap-models', help='Stage a validated air-gapped Devin models file')
    devin_airgap.add_argument('--input', type=Path, required=True)
    devin_airgap.add_argument('--output-dir', type=Path, default=Path('generated'))
    devin_airgap.add_argument('--dry-run', action='store_true')
    devin_airgap.add_argument('--force', action='store_true')
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
    opencode_models = subs.add_parser('opencode-models', help='Extract model and variant selectors from a saved OpenCode V2 response')
    opencode_models.add_argument('--input', type=Path, required=True)
    kilo_models = subs.add_parser('kilo-models', help='Extract model and variant selectors from a saved Kilo response')
    kilo_models.add_argument('--input', type=Path, required=True)
    agents = subs.add_parser('agents')
    agents.add_argument('action', nargs='?', choices=['inventory'], default='inventory')
    agents.add_argument('--root', type=Path)
    agents.add_argument('--agent')
    agents.add_argument('--surface')
    agents.add_argument('--format', choices=['json', 'text'], default='json')
    for name in ('render', 'bundle', 'provider', 'doctor', 'model', 'agent', 'acp'):
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
            render_route.add_argument('--antigravity-user-mcp', action='store_true',
                                      help='Stage a user MCP fragment for Antigravity Desktop or IDE')
            render_route.add_argument('--devin-stage-disabled', action='store_true',
                                      help='Stage selected Devin Local MCP servers as disabled')
            render_route.add_argument('--lmstudio-responses', action='store_true',
                                      help='Stage selected LM Studio MCP servers for POST /v1/responses')
            render_route.add_argument('--lmstudio-mcp-plugins', action='store_true',
                                      help='Reference selected preconfigured LM Studio MCP servers in native chat')
            command.add_argument('--lmstudio-allow-tool', action='append', metavar='SERVER:TOOL',
                                 help='Allow an exact tool on a selected LM Studio MCP request server')
            command.add_argument('--antigravity-sdk-allow-tool', action='append', metavar='SERVER:TOOL',
                                 help='Allow an exact tool on a selected Antigravity SDK MCP server')
            _perplexity_mcp_options(command)
            command.add_argument('--devin-disable-tool', action='append', metavar='SERVER:TOOL',
                                 help='Hide an exact tool on a selected Devin Local MCP server')
            command.add_argument('--kiro-disable-tool', action='append', metavar='SERVER:TOOL',
                                 help='Hide an exact tool on a selected Kiro CLI or IDE MCP server')
        elif name == 'bundle':
            command.add_argument('--skill', action='append', default=[])
            bundle_route = command.add_mutually_exclusive_group()
            bundle_route.add_argument('--chatgpt-plugin', action='store_true',
                                      help='Stage a portable plugin package for ChatGPT or Work')
            command.add_argument('--chatgpt-plugin-name', metavar='NAME',
                                 help='Set a distinct portable ChatGPT plugin identifier')
            command.add_argument('--chatgpt-workspace-marketplace', action='store_true',
                                 help='Stage the GitHub marketplace handoff for ChatGPT workspace import')
            command.add_argument('--chatgpt-app', action='append', metavar='NAME:ID',
                                 help='Reference an existing app by its app ID or plugin_ listing ID')
            bundle_route.add_argument('--perplexity-plugin', action='store_true',
                                      help='Stage a portable Perplexity app plugin package')
            bundle_route.add_argument('--perplexity-inline-skills', action='store_true',
                                      help='Stage selected Skills for a Perplexity Agent API request')
            bundle_route.add_argument('--lmstudio-preset', action='store_true',
                                      help='Stage a classic LM Studio Desktop Preset for manual import')
            bundle_route.add_argument('--lmstudio-skill-mcp', action='store_true',
                                      help='Stage selected portable Skills as a local MCP tool for LM Studio')
            command.add_argument('--lmstudio-preset-thinking', choices=['on', 'off'],
                                 help='Set thinking for a model with a compatible chat template')
            command.add_argument('--lmstudio-preset-budget', type=int, metavar='TOKENS',
                                 help='Set a reasoning budget in a classic LM Studio Desktop Preset')
            command.add_argument('--enable', action='append', default=[], metavar='SERVER',
                                 help='Include an MCP server in a staged ChatGPT or Perplexity plugin')
            command.add_argument('--enable-sse', action='append', default=[], metavar='SERVER',
                                 help='Include a legacy SSE connector in a Perplexity plugin')
        elif name in ('provider', 'model'):
            command.add_argument('--provider', required=name == 'provider')
            command.add_argument('--model')
            command.add_argument('--antigravity-adc', action='store_true',
                                 help='Stage Google Cloud ADC mode for Antigravity App or CLI')
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
                from .antigravity_sdk import SDK_ENDPOINTS

                command.add_argument('--antigravity-sdk-endpoint', choices=SDK_ENDPOINTS,
                                     help='Select a documented Antigravity Python SDK model route')
                command.add_argument('--lmstudio-generator', action='store_true',
                                     help='Stage a classic LM Studio text-chat generator plugin')
                host_catalog = command.add_mutually_exclusive_group()
                host_catalog.add_argument('--openrouter-provider-endpoints', type=Path,
                                          help='Saved OpenRouter model-specific provider endpoint listing')
                command.add_argument('--openrouter-provider-tag',
                                     help='Use an exact advertised provider tag with parameter filtering')
                command.add_argument('--openrouter-turn-effort',
                                     help='Stage an OpenRouter history item with a new reasoning effort')
                host_catalog.add_argument('--claude-catalog', type=Path, help='Saved Claude Code Agent SDK ModelInfo array')
                command.add_argument('--claude-ultracode', choices=['on', 'off'],
                                     help='Stage the Claude Code Ultracode workflow setting')
                host_catalog.add_argument('--factory-catalog', type=Path, help='Saved Factory Droid SDK listModels() array')
                command.add_argument('--factory-spec-model', help='Start Droid in specification mode with this catalog model')
                command.add_argument('--factory-spec-reasoning-effort', help='Effort for the selected specification model')
                host_catalog.add_argument('--qwen-workspace-catalog', type=Path,
                                          help='Saved Qwen daemon GET /workspace/providers response')
                host_catalog.add_argument('--kimi-catalog', type=Path, help='Saved Kimi web GET /api/v1/models response')
                host_catalog.add_argument('--copilot-catalog', type=Path, help='Saved Copilot SDK listModels() JSON array')
                host_catalog.add_argument('--devin-catalog', type=Path, help='Saved Devin Desktop GET /api/v2alpha/models response')
                host_catalog.add_argument('--bionic-catalog', type=Path, help='Saved Bionic modelSelectionOptions array')
                command.add_argument('--bionic-source', choices=BIONIC_MODEL_SOURCES,
                                     help='Disambiguate an exact model ID by Bionic picker source')
                host_catalog.add_argument('--opencode-catalog', type=Path, help='Saved OpenCode V2 GET /api/model JSON response')
                host_catalog.add_argument('--kilo-catalog', type=Path, help='Saved Kilo GET /api/model JSON response')
                command.add_argument('--model-variant', help='Named variant from a saved OpenCode V2, Kilo or Kilo Gateway catalog')
                command.add_argument('--perplexity-fallback-model', action='append', metavar='ID',
                                     help='Add an Agent API fallback model in attempt order')
                command.add_argument('--perplexity-max-output-tokens', type=int, metavar='TOKENS',
                                     help='Stage an Agent API output-token budget')
                command.add_argument('--perplexity-max-steps', type=int, metavar='STEPS',
                                     help='Stage the Agent API research-loop step limit (1–500)')
                command.add_argument('--perplexity-service-tier', choices=PERPLEXITY_SERVICE_TIERS,
                                     help='Stage an Agent API processing tier')
                perplexity_defaults = command.add_mutually_exclusive_group()
                perplexity_defaults.add_argument('--perplexity-effort-preset', metavar='NAME[@VERSION]',
                                                 help='Select a dynamic or pinned Agent API effort preset')
                perplexity_defaults.add_argument('--perplexity-profile', metavar='ID',
                                                 help='Reference a saved Agent API profile')
                command.add_argument('--perplexity-profile-version', metavar='VERSION',
                                     help='Select a saved profile version or latest')
                command.add_argument('--profile', metavar='WEAVER_PROFILE',
                                     help='Weaver profile for Perplexity Agent API Skills or MCP, or Orchestrator MCP tools')
                command.add_argument('--skill', action='append', default=[], metavar='SKILL',
                                     help='Select a portable Skill for the Perplexity Agent API or Orchestrator')
                command.add_argument('--enable', action='append', default=[], metavar='SERVER',
                                     help='Include an approval-required MCP server in the staged Perplexity Agent API request')
                _perplexity_mcp_options(command)
                command.add_argument('--orchestrator-mcp-list', action='append', default=[], metavar='SERVER=FILE',
                                     help='Saved MCP tools/list response from a profile-allowed Orchestrator tool source')
                command.add_argument('--orchestrator-mcp-tool', action='append', default=[], metavar='SERVER:TOOL',
                                     help='Expose one exact tool from a saved Orchestrator MCP tool list')
                command.add_argument('--kimi-workspace-id', help='Registered local workspace for Kimi web session staging')
                command.add_argument('--kimi-agent-profile', help='Existing Kimi main-agent profile name')
                command.add_argument('--kimi-subagent-pool', action='store_true',
                                     help='Stage a Kimi subagent model pool instead of a web session request')
                command.add_argument('--kimi-subagent-alias', action='append', default=[],
                                     help='Add an alias to the Kimi subagent model pool')
                command.add_argument('--qwen-auth-type', help='Filter the saved Qwen workspace catalog by authentication type')
                zed_target = command.add_mutually_exclusive_group()
                zed_target.add_argument('--zed-subagent', action='store_true',
                                        help='Stage the selected model as Zed Agent’s subagent default')
                zed_target.add_argument('--zed-profile', metavar='ID',
                                        help='Stage a named read-only Zed Agent Profile with the selected model')
                command.add_argument('--zed-speed', choices=ZED_SPEEDS,
                                     help='Stage request speed for a supported Zed OpenAI or Anthropic model')
                kiro_output = command.add_mutually_exclusive_group()
                kiro_output.add_argument('--kiro-model-defaults', action='store_true',
                                         help='Stage Kiro CLI V3 per-model reasoning settings')
                kiro_output.add_argument('--kiro-acp', action='store_true',
                                         help='Stage Kiro CLI V3 ACP launch arguments and initial model options')
                command.add_argument('--antigravity-custom-model', metavar='ALIAS',
                                     help='Stage an Antigravity CLI Gemini-compatible custom model')
                command.add_argument('--kiro-thinking-display', choices=['summarized', 'omitted'],
                                     help='Stage Kiro CLI adaptive thinking visibility')
                command.add_argument('--kiro-max-output-tokens', type=int,
                                     help='Stage Kiro CLI per-model max_tokens (output limit)')
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
            command.add_argument('--cursor-catalog', type=Path, help='Saved Cursor SDK models.list() array')
            command.add_argument('--cursor-param', action='append', help='Select a Cursor subagent parameter as ID=VALUE')
            command.add_argument('--cursor-variant', metavar='NAME', help='Select an exact Cursor preset display name')
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
            command.add_argument('--chatgpt-workspace-agent', action='store_true',
                                 help='Stage a prompt and selected Skills for the ChatGPT Workspace Agent builder')
            command.add_argument('--allow-skill', action='append',
                                 help='Select a portable Skill for the selected supported agent or builder')
            command.add_argument('--allow-mcp', action='append',
                                 help='Name a profile-allowlisted MCP server for Factory, Qoder, or VS Code Copilot')
            command.add_argument('--allow-mcp-tool', action='append', metavar='SERVER:TOOL',
                                 help='Select an exact MCP tool for Factory CLI, Qoder CLI, or VS Code Copilot')
            command.add_argument('--hermes-profile', action='store_true', help='Stage a Hermes profile distribution')
            command.add_argument('--hermes-profile-route', action='store_true',
                                 help='Stage a separate Hermes parent delegation fragment for this profile')
            command.add_argument('--profile-skill', action='append', help='Bundle a portable Skill in a staged Hermes profile')
            command.add_argument('--mcp-profile', help='Weaver profile ID that allowlists the selected MCP servers')
            command.add_argument('--profile-mcp', action='append', help='Bundle an allowlisted MCP server, disabled')
            command.add_argument('--profile-context-size', type=int, help='Explicit context length for a Hermes LM Studio profile')
            command.add_argument('--reasoning-effort')
            command.add_argument('--reasoning-budget', type=int, help='Stage numeric effort for a Qoder CLI subagent')
            command.add_argument('--codex-reasoning-summary', choices=CODEX_REASONING_SUMMARIES,
                                 help='Set the Codex custom agent reasoning summary')
            command.add_argument('--codex-verbosity', choices=CODEX_VERBOSITIES,
                                 help='Set the Codex custom agent model verbosity')
        elif name == 'acp':
            command.add_argument('--name', required=True, help='Name in the Air in IDEs launch picker')
            command.add_argument('--command', required=True, help='Absolute path to an existing ACP executable')
            command.add_argument('--arg', action='append', default=[], help='Pass one ACP argument')
            command.add_argument('--use-idea-mcp', action='store_true', help='Allow Air in IDEs integrated MCP tools')
            command.add_argument('--use-custom-mcp', action='store_true', help='Allow user-configured MCP servers')
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
    profile_id = getattr(args, 'profile', None)
    if profile_id is None:
        profile_id = getattr(args, 'mcp_profile', None)
    if profile_id is not None:
        if not safe_name(profile_id):
            raise WeaverError('Invalid profile name; use an allowlisted profile ID')
        paths.append('catalogs/mcp-servers.json')
        if args.root is None:
            paths.append(f'profiles/{profile_id}.json')
    if (args.cmd == 'bundle' or (args.cmd == 'model' and args.skill)
            or (args.cmd == 'agent' and (args.allow_skill or args.profile_skill))):
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
             copilot_portable: bool = False, antigravity_user_mcp: bool = False,
             devin_stage_disabled: bool = False, lmstudio_responses: bool = False,
             lmstudio_plugins: bool = False) -> RenderedArtifact:
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
        if (spec.id, surface) == ('kiro', 'cli'):
            lines.append('Review any disabledTools filters before enabling the server.')
        if (spec.id, surface) == ('kiro', 'ide'):
            lines.append('Review any disabledTools filters before merging the selected servers into the project.')
        if (spec.id, surface) == ('antigravity', 'sdk'):
            lines.append('After review, pass build_mcp_servers() from sdk/mcp_config.py as the SDK mcp_servers list.')
        elif antigravity_user_mcp:
            client = 'App' if surface == 'desktop' else 'IDE'
            lines.extend([
                'After review, merge individual server entries into ~/.gemini/config/mcp_config.json.',
                f'This file applies to every {client} project for this user. Preserve existing entries and settings.',
                f'Selected servers can start when the {client} refreshes this file. Review their tool access before merging.',
            ])
        elif target.config_scope == 'user':
            lines.extend(['User-scope fragment only: use the vendor settings interface to review and manually merge',
                          f'the selected entries at {target.documented_location or "the documented user settings location"}.',
                          'Do not copy this fragment into a project or replace the entire user configuration.'])
        elif (spec.id, surface) == ('openhands', 'canvas'):
            lines.extend([
                'Each JSON file is one disabled server body for POST /api/settings/mcp/<server-id>.',
                'Use the filename stem as the server ID. Check for existing entries on the intended local Agent Server.',
                'After review and authorized import, enable only the intended entries in Customize > MCP Servers.',
                'An empty selection stages only this handoff. Weaver does not contact the backend.',
            ])
        elif target.config_scope == 'request':
            lines.extend(['API request fragment only: merge into the documented request after review.',
                          'No request was sent and no local server, connector, or MCP setting was enabled.'])
            if (spec.id, surface) == ('lm-studio', 'api'):
                endpoint = '/v1/responses' if lmstudio_responses else '/api/v1/chat'
                lines.append(f'Merge the reviewed fragment into POST {endpoint}.')
                if lmstudio_plugins:
                    lines.extend([
                        'Review matching server IDs in the target mcp.json before sending the request.',
                        'The target server must require authentication and allow calling servers from mcp.json.',
                        'Supply an authorized token through the request client.',
                    ])
                else:
                    lines.append('The target server must allow per-request MCP.')
                lines.append('A server without allowed_tools exposes all its tools.')
            elif (spec.id, surface) == ('perplexity', 'agent-api'):
                lines.extend([
                    'Merge the reviewed fragment into POST /v1/agent. Each staged server requests approval for every call.',
                    'A server without allowed_tools exposes its full discovered tool list.',
                    'With defer_loading, budget steps for tool search, invocation, and the answer.',
                    'Review each proposed call and resend the same tool declarations with the approval decisions.',
                    '- https://docs.perplexity.ai/docs/agent-api/tools/mcp',
                ])
        elif (spec.id, surface) == ('lm-studio', 'bionic'):
            lines.extend([
                f'Use bionic-setup.md to enter each reviewed server in {target.documented_location}.',
                'The values are for manual entry. Review matching names and preserve existing settings.',
            ])
        elif (spec.id, surface) == ('perplexity', 'app'):
            lines.extend([
                'Use perplexity/mcp/app-setup.md to enter selected connectors through Plugins > Add custom.',
                'Local commands apply to the macOS app with the PerplexityXPC helper.',
                'Review account or organization scope, tool permissions, and authentication before enabling a connector.',
            ])
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
            if devin_stage_disabled:
                lines.append('Selected servers are staged disabled. Use Customize > MCPs to review and enable individual servers.')
            lines.append('Review server and tool permissions in Devin Local. A disabledTools entry hides a named tool.')
        if (spec.id, surface) == ('jetbrains-air', 'ide'):
            lines.extend([
                'In AI > Air > MCP Servers, use Edit MCPs and merge only the reviewed entries into mcpServers.',
                'Reload the server list and enable only the intended servers before starting a new session.',
                'This user file supplies servers to compatible agents across projects. Review each URL or '
                'command and its arguments before merging.',
            ])
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
        if (spec.id, surface) == ('zed', 'ide'):
            lines.extend(['Zed entries are staged under context_servers with enabled: false.',
                          'Review and merge individual entries into Zed settings, then enable only the intended servers.'])
        if (spec.id, surface) == ('openhands', 'cli'):
            lines.extend(['OpenHands entries are staged under mcpServers with enabled: false.',
                          'Review and merge individual entries into mcp.json, then run openhands mcp enable NAME for each intended server.'])
        if (spec.id, surface) == ('cursor', 'cli'):
            lines.extend([
                'Review the project entries before running agent mcp list --workspace PROJECT.',
                'Approve only the intended server with agent mcp enable NAME --workspace PROJECT.',
                'Use agent mcp list-tools NAME --workspace PROJECT to verify discovery.',
            ])
        if (spec.id, surface) == ('antigravity', 'cli'):
            lines.extend([
                'Review and merge individual entries into .agents/mcp_config.json in the intended workspace.',
                'Use /mcp in a trusted workspace session to inspect workspace servers.',
                'For headless calls, allow the exact tool with mcp(SERVER/TOOL) under permissions.allow in reviewed CLI settings.',
                'CLI permissions: https://antigravity.google/docs/cli/permissions',
            ])
        if (spec.id, surface) == ('antigravity', 'desktop'):
            if not antigravity_user_mcp:
                lines.append('Review and merge individual entries into .agents/mcp_config.json for a folder in the App project.')
            lines.extend([
                'Inspect the selected servers under Customizations > Installed > MCP Servers in the Antigravity App.',
                'App MCP settings: https://antigravity.google/docs/mcp',
            ])
        if antigravity_user_mcp and surface == 'ide':
            lines.extend(['Open MCP Servers > Manage MCP Servers in the IDE agent panel to review the merged entries.',
                          'IDE MCP settings: https://antigravity.google/docs/mcp?tab=ide'])
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
    name = label or ('MCP-USER' if antigravity_user_mcp else 'MCP' if mcp else 'OS')
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


def _compose_perplexity_request(
    args: argparse.Namespace, root: Path, spec: IntegrationSpec, surface: str, artifacts: list[RenderedArtifact],
) -> list[RenderedArtifact]:
    from .skills import render_instruction_bundle

    profile = load_json(root / 'profiles' / f'{args.profile}.json')
    catalog = load_json(root / 'catalogs/mcp-servers.json')
    errors = validate_catalog(catalog) + validate_profile(profile, catalog)
    if errors:
        raise WeaverError('; '.join(errors))
    extra_fields: dict[str, Any] = {}
    notes = ['', '## Request composition', '',
             'Review the combined `model-selection.json` and supply input and credentials separately.']
    if args.skill:
        bundle = render_instruction_bundle(spec, surface, profile, args.skill, root, perplexity_inline_skills=True)
        skills = next(item for item in bundle if item.kind == 'skill-request')
        extra_fields['skills'] = json.loads(skills.content)['skills']
        notes.extend([
            "Sending the Skills enables Perplexity's request sandbox. Allow steps to load and use each Skill.",
            "An explicit model or fallback chain defaults to one step without a saved Profile or effort preset. "
            "Set the research-loop limit with `--perplexity-max-steps` for the intended workflow.",
            '- https://docs.perplexity.ai/docs/agent-api/skills',
        ])
    if args.enable:
        mcp, = render_mcp(spec, surface, profile, catalog, args.enable, perplexity_tools=args.perplexity_allow_tool,
                          perplexity_deferred_servers=args.perplexity_defer_tools)
        extra_fields['tools'] = json.loads(mcp.content)['tools']
        notes.extend([
            'Each selected MCP server requires approval for every call. Servers without an allowlist expose their full tool list.',
            'When sending an approval decision, resend the reviewed tool declarations unchanged.',
            '- https://docs.perplexity.ai/docs/agent-api/tools/mcp',
        ])
    return _merge_perplexity_request(artifacts, extra_fields, notes)


def _orchestrator_mcp_selection(args: argparse.Namespace) -> tuple[dict[str, Path], dict[str, list[str]]]:
    paths: dict[str, Path] = {}
    for value in args.orchestrator_mcp_list:
        server, separator, filename = value.partition('=')
        if not separator or not filename or not safe_name(server) or server in paths:
            raise WeaverError('Use one --orchestrator-mcp-list SERVER=FILE per selected server')
        paths[server] = Path(filename)
    tools: dict[str, list[str]] = {}
    for value in args.orchestrator_mcp_tool:
        server, separator, name = value.partition(':')
        if not separator or server not in paths or not name:
            raise WeaverError('Use --orchestrator-mcp-tool SERVER:TOOL with a matching saved tool list')
        tools.setdefault(server, []).append(name)
    if paths.keys() != tools.keys():
        raise WeaverError('Select at least one exact tool from each saved MCP tool list')
    return paths, tools


def _compose_orchestrator_tools(
    args: argparse.Namespace, root: Path, artifacts: list[RenderedArtifact],
) -> list[RenderedArtifact]:
    from .models import load_model_providers, orchestrator_function, orchestrator_mcp_tools
    from .skills import portable_skill_texts

    paths, tools = _orchestrator_mcp_selection(args)
    if args.profile is not None:
        profile = load_json(root / 'profiles' / f'{args.profile}.json')
        catalog = load_json(root / 'catalogs/mcp-servers.json')
        select_servers(profile, catalog, list(paths))
    protocol = args.protocol or load_model_providers(root)['perplexity-orchestrator']['protocols'][0]
    selection = next(item for item in artifacts if item.kind == 'model-selection')
    declarations: list[dict[str, Any]] = []
    sidecars: list[RenderedArtifact] = []
    notes = ['', '## Client-side tools', '']
    if paths:
        declarations, tool_map = orchestrator_mcp_tools(protocol, paths, tools)
        sidecars.append(RenderedArtifact(selection.relative_path.with_name('mcp-tool-map.json'),
                                         json_bytes(tool_map), 'mcp-tool-map'))
        notes.extend([
            'Review the selected tool descriptions and schemas before sending the request.',
            'Use `mcp-tool-map.json` to resolve each returned function name to its original MCP server and tool.',
            'Your client owns the server connection, invocation approval, argument validation, and `tools/call`.',
            'Reject unmapped calls and review what tool output may be sent to the model.',
            '- https://modelcontextprotocol.io/specification/2025-11-25/server/tools',
        ])
    if args.skill:
        descriptions, instructions = portable_skill_texts(root, args.skill)
        function = 'weaver_load_skill'
        description = ('Load one Skill when it applies to the current task. Use a Skill once per conversation.\n'
                       + '\n'.join(f'- {name} — {text}' for name, text in descriptions.items()))
        schema = {'type': 'object', 'properties': {'name': {'type': 'string', 'enum': list(descriptions)}},
                  'required': ['name'], 'additionalProperties': False}
        declarations.append(orchestrator_function(protocol, function, description, schema))
        sidecars.append(RenderedArtifact(selection.relative_path.with_name('skill-tool-map.json'),
                                         json_bytes({'function': function, 'instructions': instructions}), 'skill-tool-map'))
        notes.extend([
            'Keep `skill-tool-map.json` in the client. The initial request sends Skill names and descriptions.',
            'For a `weaver_load_skill` call, validate that the arguments contain only an exact listed `name`. '
            'Return only that entry from the map as the tool result after reviewing its instructions.',
            'Keep the loaded result in conversation history for later turns. Handle repeat calls without sending '
            'the full instructions again when they are already present.',
            'The tool returns instruction text. Script execution and supporting-file access require their own client implementation.',
            '- https://agentskills.io/client-implementation/adding-skills-support',
        ])
    output_handoff = {
        'openai-chat': 'Append the assistant tool call and a tool-role message with its `tool_call_id`.',
        'openai-responses': 'Include the function call and a `function_call_output` with the same `call_id` in the full input history.',
        'anthropic-messages': 'Append the assistant `tool_use` block and a user `tool_result` block with its `tool_use_id`.',
    }[protocol]
    notes.extend(['', output_handoff])
    return [*_merge_perplexity_request(artifacts, {'tools': declarations}, notes), *sidecars]


def _merge_perplexity_request(
    artifacts: list[RenderedArtifact], extra_fields: dict[str, Any], notes: list[str],
) -> list[RenderedArtifact]:
    selection = next(item for item in artifacts if item.kind == 'model-selection')
    handoff = next(item for item in artifacts if item.kind == 'model-setup')
    model_fields = json.loads(selection.content)
    if model_fields.keys() & extra_fields.keys():
        raise WeaverError('Perplexity request fragments have overlapping fields')
    merged = replace(selection, content=json_bytes(model_fields | extra_fields))
    annotated = replace(handoff, content=handoff.content + ('\n'.join(notes) + '\n').encode('utf-8'))
    return [merged if item is selection else annotated if item is handoff else item for item in artifacts]


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
    from .models import load_model_providers, model_inventory, read_model_snapshot, read_openrouter_provider_endpoints

    if args.cmd == 'coverage':
        data = coverage_report(root)
        data['model_catalogs'] = model_inventory(root)
        data['custom_agents'] = agent_inventory(root)
        data['mcp_transports'] = {'stdio': {
            'documented_adapters': stdio_inventory(),
            'runtime_verified': False,
            'unestablished_file_exports': [],
            'remote_only_requests': ['perplexity/agent-api', 'lm-studio/api'],
            'preconfigured_stdio_request_references': ['lm-studio/api'],
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
        if args.openrouter_provider_endpoints is not None:
            if args.provider != 'openrouter' or args.agent is not None:
                raise WeaverError('Provider endpoint inspection requires --provider openrouter')
            data = read_openrouter_provider_endpoints(args.openrouter_provider_endpoints)
        elif args.input is not None:
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
    if (args.cmd == 'model' and args.antigravity_sdk_endpoint is not None
            and (spec.id, surface) != ('antigravity', 'sdk')):
        raise WeaverError('--antigravity-sdk-endpoint requires the Antigravity SDK surface')
    if args.cmd in ('provider', 'model'):
        if args.antigravity_adc and (spec.id != 'antigravity' or args.provider != 'google'
                                    or surface not in {'desktop', 'cli'}):
            raise WeaverError('Antigravity ADC requires --agent antigravity, --provider google and App or CLI')
        if args.antigravity_adc and args.cmd == 'model' and surface != 'cli':
            raise WeaverError('Use provider --antigravity-adc for the App and choose a model through its picker')
        if args.cmd == 'provider' and args.model is None and not args.antigravity_adc:
            raise WeaverError('Provider staging requires --model')
    if args.cmd == 'model' and args.lmstudio_generator and (spec.id, surface) != ('lm-studio', 'desktop'):
        raise WeaverError('LM Studio Generator staging requires the classic Desktop surface')
    if args.cmd == 'acp':
        from .agents import render_acp_registration

        _emit(args, render_acp_registration(
            spec, surface, name=args.name, command=args.command, arguments=args.arg, target_os=target_os,
            use_idea_mcp=args.use_idea_mcp, use_custom_mcp=args.use_custom_mcp,
        ))
        return 0
    perplexity_request = args.cmd == 'model' and (
        args.profile is not None or args.skill or args.enable or args.perplexity_allow_tool or args.perplexity_defer_tools
    )
    orchestrator_route = args.cmd == 'model' and (spec.id, surface, args.provider) == (
        'perplexity', 'orchestrator', 'perplexity-orchestrator',
    )
    orchestrator_mcp = args.cmd == 'model' and (args.orchestrator_mcp_list or args.orchestrator_mcp_tool)
    orchestrator_tools = orchestrator_mcp or (orchestrator_route and args.skill)
    if orchestrator_tools:
        if not orchestrator_route:
            raise WeaverError('Orchestrator MCP tool options require the Perplexity Orchestrator route')
        if orchestrator_mcp and (args.profile is None or not (args.orchestrator_mcp_list and args.orchestrator_mcp_tool)):
            raise WeaverError('Use --profile, --orchestrator-mcp-list and --orchestrator-mcp-tool together')
        if args.enable or args.perplexity_allow_tool or args.perplexity_defer_tools:
            raise WeaverError('Agent API request options are unavailable for Orchestrator tools')
    elif perplexity_request:
        if (spec.id, surface, args.provider) != ('perplexity', 'agent-api', 'perplexity-agent'):
            raise WeaverError('Perplexity request options require the Perplexity Agent API route')
        if args.profile is None or not (args.skill or args.enable):
            raise WeaverError('Use --profile with at least one --skill or --enable selection')
        if (args.perplexity_allow_tool or args.perplexity_defer_tools) and not args.enable:
            raise WeaverError('Perplexity MCP controls require a server selected with --enable')
    if args.cmd == 'model' and any(value is not None for value in (
        args.perplexity_fallback_model, args.perplexity_max_output_tokens, args.perplexity_max_steps,
        args.perplexity_service_tier,
        args.perplexity_effort_preset, args.perplexity_profile, args.perplexity_profile_version,
    )):
        if (spec.id, surface, args.provider) != ('perplexity', 'agent-api', 'perplexity-agent'):
            raise WeaverError('Perplexity request options require the Perplexity Agent API route')
    if args.cmd == 'model' and all(value is None for value in (
        args.model, args.perplexity_effort_preset, args.perplexity_profile,
    )):
        if args.perplexity_profile_version is not None:
            raise WeaverError('A Perplexity profile version requires a saved profile ID')
        raise WeaverError('Model staging requires --model, a Perplexity effort preset, or a saved Perplexity profile')
    kilo_gateway_model = args.cmd == 'model' and spec.id == 'kilo' and args.provider == 'kilo-gateway'
    if args.cmd == 'model' and (spec.id, surface) == ('kilo', 'gateway') and not kilo_gateway_model:
        raise WeaverError('The Kilo Gateway model target requires --provider kilo-gateway')
    if args.cmd in {'model', 'provider'} and args.provider == 'kilo-gateway' and not kilo_gateway_model:
        raise WeaverError('Kilo Gateway catalog selections require model --agent kilo')
    if (args.cmd == 'model' and args.model_variant is not None
            and args.opencode_catalog is None and args.kilo_catalog is None and not kilo_gateway_model):
        raise WeaverError('--model-variant requires an OpenCode, Kilo, or Kilo Gateway catalog')
    if args.cmd == 'model' and args.bionic_source is not None and args.bionic_catalog is None:
        raise WeaverError('--bionic-source requires --bionic-catalog')
    if args.cmd == 'model' and (args.openrouter_provider_tag is not None or args.openrouter_provider_endpoints is not None):
        if (spec.id, surface, args.provider) != ('openrouter', 'gateway', 'openrouter'):
            raise WeaverError('OpenRouter provider filtering requires the OpenRouter gateway target')
    if (args.cmd == 'model' and args.openrouter_turn_effort is not None
            and (spec.id, surface, args.provider) != ('openrouter', 'gateway', 'openrouter')):
        raise WeaverError('Mid-conversation effort requires the OpenRouter gateway target')
    if args.cmd == 'model' and args.claude_ultracode is not None and args.claude_catalog is None:
        raise WeaverError('--claude-ultracode requires --claude-catalog')
    if args.cmd == 'model' and (args.zed_profile is not None or args.zed_subagent):
        if ((spec.id, surface) != ('zed', 'ide') or args.provider not in ZED_NATIVE_PROTOCOLS
                or args.protocol not in {None, ZED_NATIVE_PROTOCOLS[args.provider]}):
            raise WeaverError('Zed profile and subagent settings require a supported native Zed IDE model')
    if (args.cmd == 'model' and args.zed_speed is not None
            and ((spec.id, surface) != ('zed', 'ide') or args.provider not in ('openai', 'anthropic'))):
        raise WeaverError('--zed-speed requires a native Zed IDE OpenAI or Anthropic model')
    if (args.cmd == 'model' and args.antigravity_custom_model is not None
            and (spec.id, surface) != ('antigravity', 'cli')):
        raise WeaverError('--antigravity-custom-model requires the Antigravity CLI surface')
    if (args.cmd == 'model' and args.kiro_thinking_display is not None
            and ((spec.id, surface) != ('kiro', 'cli') or not args.kiro_model_defaults)):
        raise WeaverError('--kiro-thinking-display requires Kiro CLI --kiro-model-defaults')
    if (args.cmd == 'model' and args.kiro_max_output_tokens is not None
            and ((spec.id, surface) != ('kiro', 'cli') or not args.kiro_model_defaults)):
        raise WeaverError('--kiro-max-output-tokens requires Kiro CLI --kiro-model-defaults')
    if args.cmd == 'model' and args.kiro_acp and (spec.id, surface) != ('kiro', 'cli'):
        raise WeaverError('--kiro-acp requires the Kiro CLI surface')
    if args.cmd == 'model' and args.qwen_auth_type is not None and args.qwen_workspace_catalog is None:
        raise WeaverError('Qwen model authentication filtering requires --qwen-workspace-catalog')
    if (args.cmd == 'model' and args.factory_catalog is None
            and (args.factory_spec_model is not None or args.factory_spec_reasoning_effort is not None)):
        raise WeaverError('Factory Droid specification options require --factory-catalog')
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
                                   opencode_v2=args.opencode_v2, copilot_portable=args.copilot_portable,
                                   antigravity_user_mcp=args.antigravity_user_mcp,
                                   devin_stage_disabled=args.devin_stage_disabled, devin_disabled_tools=args.devin_disable_tool,
                                   kiro_disabled_tools=args.kiro_disable_tool,
                                   lmstudio_responses=args.lmstudio_responses, lmstudio_plugins=args.lmstudio_mcp_plugins,
                                   lmstudio_tools=args.lmstudio_allow_tool,
                                   perplexity_tools=args.perplexity_allow_tool,
                                   perplexity_deferred_servers=args.perplexity_defer_tools,
                                   antigravity_sdk_tools=args.antigravity_sdk_allow_tool)
        else:
            from .skills import (
                render_chatgpt_plugin,
                render_instruction_bundle,
                render_lmstudio_skill_mcp,
                render_perplexity_plugin,
            )

            catalog = load_json(root / 'catalogs/mcp-servers.json')
            if (args.chatgpt_workspace_marketplace or args.chatgpt_app) and not args.chatgpt_plugin:
                raise WeaverError('ChatGPT marketplace and app options require --chatgpt-plugin')
            if args.chatgpt_plugin_name is not None and not args.chatgpt_plugin:
                raise WeaverError('--chatgpt-plugin-name requires --chatgpt-plugin')
            if args.chatgpt_plugin or args.perplexity_plugin:
                if args.lmstudio_preset_thinking is not None or args.lmstudio_preset_budget is not None:
                    raise WeaverError('LM Studio preset reasoning controls require --lmstudio-preset')
                if args.chatgpt_plugin:
                    if args.enable_sse:
                        raise WeaverError('Use --enable-sse only with --perplexity-plugin')
                    artifacts = render_chatgpt_plugin(
                        spec, surface, root, profile, catalog, args.skill, args.enable,
                        workspace_marketplace=args.chatgpt_workspace_marketplace, app_selectors=args.chatgpt_app,
                        plugin_name=args.chatgpt_plugin_name,
                    )
                else:
                    artifacts = render_perplexity_plugin(spec, surface, root, profile, catalog,
                                                         args.skill, args.enable, args.enable_sse)
            else:
                if args.enable or args.enable_sse:
                    raise WeaverError('Bundle MCP selection requires --chatgpt-plugin or --perplexity-plugin')
                errors = validate_catalog(catalog) + validate_profile(profile, catalog)
                if errors:
                    raise WeaverError('; '.join(errors))
                if args.lmstudio_skill_mcp:
                    if args.lmstudio_preset_thinking is not None or args.lmstudio_preset_budget is not None:
                        raise WeaverError('LM Studio preset reasoning controls require --lmstudio-preset')
                    artifacts = render_lmstudio_skill_mcp(spec, surface, root, profile, args.skill, args.output_dir)
                else:
                    artifacts = render_instruction_bundle(
                        spec, surface, profile, args.skill, root, lmstudio_preset=args.lmstudio_preset,
                        lmstudio_thinking=args.lmstudio_preset_thinking, lmstudio_budget=args.lmstudio_preset_budget,
                        perplexity_inline_skills=args.perplexity_inline_skills,
                    )
    elif args.cmd == 'model' and (spec.id, surface) == ('antigravity', 'sdk'):
        from .antigravity_sdk import render_sdk_model

        if args.antigravity_sdk_endpoint is None:
            raise WeaverError('Antigravity SDK model staging requires --antigravity-sdk-endpoint')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in (
                'provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode',
                'context_size', 'batch_endpoint', 'copilot_catalog', 'opencode_catalog', 'kilo_catalog',
                'qoder_catalog', 'claude_catalog', 'factory_catalog', 'qwen_workspace_catalog',
                'kimi_catalog', 'devin_catalog', 'bionic_catalog',
            )
        ):
            raise WeaverError('Antigravity SDK model staging accepts a model, SDK endpoint and optional thinking level')
        artifacts = render_sdk_model(args.model, args.antigravity_sdk_endpoint, args.reasoning_effort)
    elif kilo_gateway_model:
        from .kilo_gateway_models import render_kilo_gateway_selection

        if args.catalog is None or args.model is None:
            raise WeaverError('Kilo Gateway model selection requires --catalog and an exact --model ID')
        if args.qoder_settings or args.kiro_model_defaults or any(
            getattr(args, key) is not None for key in (
                'protocol', 'reasoning_effort', 'reasoning_budget', 'reasoning', 'reasoning_mode', 'context_size',
                'batch_endpoint',
            )
        ):
            raise WeaverError('Kilo Gateway model selection uses the catalog model and optional named variant')
        artifacts = render_kilo_gateway_selection(root, args.catalog, args.model, args.model_variant, surface=surface)
    elif args.cmd == 'model' and (args.opencode_catalog is not None or args.kilo_catalog is not None):
        from .opencode_models import render_kilo_desktop_selection, render_kilo_run, render_opencode_run

        if args.kilo_catalog is not None:
            catalog, product = args.kilo_catalog, 'kilo'
            render_selection = render_kilo_desktop_selection if surface == 'desktop' else render_kilo_run
            allowed_surfaces = {'cli', 'desktop'}
        else:
            catalog, product, render_selection = args.opencode_catalog, 'opencode', render_opencode_run
            allowed_surfaces = {'cli'}
        if spec.id != product or surface not in allowed_surfaces:
            if product == 'kilo':
                raise WeaverError('The selected model catalog requires the Kilo CLI or Desktop model target')
            raise WeaverError(f'The selected model catalog requires the {product} CLI target')
        if any(getattr(args, option) is not None for option in
               ('provider', 'catalog', 'protocol', 'reasoning_effort', 'reasoning_budget', 'reasoning',
                'reasoning_mode', 'context_size', 'batch_endpoint')) or (
            args.zed_subagent or args.kiro_model_defaults or args.qoder_settings
        ):
            raise WeaverError('This model staging route uses a catalog selector and optional named variant')
        artifacts = render_selection(catalog, args.model, args.model_variant)
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
        ultracode = None if args.claude_ultracode is None else args.claude_ultracode == 'on'
        artifacts = render_claude_model(args.claude_catalog, surface, args.model, args.reasoning_effort,
                                        ultracode=ultracode)
    elif args.cmd == 'model' and args.factory_catalog is not None:
        from .factory_models import render_factory_exec

        if (spec.id, surface) != ('factory', 'cli'):
            raise WeaverError('A Factory Droid SDK catalog requires the Factory CLI target')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode',
             'context_size', 'batch_endpoint', 'qoder_catalog')
        ):
            raise WeaverError('A Factory Droid SDK catalog uses a model ID and optional reasoning effort')
        artifacts = render_factory_exec(args.factory_catalog, args.model, args.reasoning_effort,
                                        spec_model=args.factory_spec_model, spec_effort=args.factory_spec_reasoning_effort)
    elif args.cmd == 'model' and args.devin_catalog is not None:
        from .devin_models import render_devin_model

        if (spec.id, surface) != ('devin', 'desktop-local'):
            raise WeaverError('A Devin Desktop catalog requires the Devin Desktop Local target')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_effort', 'reasoning_budget', 'reasoning',
             'reasoning_mode', 'context_size', 'batch_endpoint')
        ):
            raise WeaverError('Devin Desktop staging uses an exact model UID and the session picker variant')
        artifacts = render_devin_model(args.devin_catalog, args.model)
    elif args.cmd == 'model' and args.bionic_catalog is not None:
        from .bionic_models import render_bionic_model

        if (spec.id, surface) != ('lm-studio', 'bionic'):
            raise WeaverError('A Bionic picker catalog requires the LM Studio Bionic target')
        if any(getattr(args, option) is not None for option in (
            'provider', 'catalog', 'protocol', 'reasoning_budget', 'reasoning', 'reasoning_mode',
            'context_size', 'batch_endpoint',
        )):
            raise WeaverError('Bionic staging uses a picker model ID, source, and optional native reasoning level')
        artifacts = render_bionic_model(args.bionic_catalog, args.model, source=args.bionic_source,
                                       effort=args.reasoning_effort)
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
                                    'context_size', 'batch_endpoint', 'qoder_catalog')):
            raise WeaverError('Kiro CLI model staging uses Kiro model IDs, effort, and persistent thinking settings')
        artifacts = render_kiro_cli_model(args.model, args.reasoning_effort, persistent=args.kiro_model_defaults,
                                         thinking_mode=args.reasoning_mode, thinking_display=args.kiro_thinking_display,
                                         max_output_tokens=args.kiro_max_output_tokens, acp=args.kiro_acp)
    elif args.cmd == 'model' and (spec.id, surface) == ('cursor', 'cli'):
        from .models import render_cursor_cli_model

        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning_effort', 'reasoning_budget', 'reasoning',
             'reasoning_mode', 'context_size', 'batch_endpoint', 'qoder_catalog')
        ):
            raise WeaverError('Cursor CLI session staging accepts an explicit Cursor model ID')
        artifacts = render_cursor_cli_model(args.model)
    elif args.cmd == 'provider' and args.antigravity_adc:
        from .models import render_antigravity_adc

        if any(value is not None for value in (
            args.model, args.catalog, args.protocol, args.reasoning_effort, args.reasoning_budget,
            args.reasoning, args.reasoning_mode, args.context_size, args.batch_endpoint,
        )):
            raise WeaverError('Antigravity ADC provider staging accepts authentication settings only')
        artifacts = render_antigravity_adc(surface)
    elif args.cmd == 'model' and (spec.id, surface) == ('antigravity', 'cli'):
        from .models import render_antigravity_adc, render_antigravity_cli_model

        custom = args.antigravity_custom_model is not None
        if args.provider not in {None, 'google'}:
            raise WeaverError('Antigravity CLI provider staging supports google for Gemini API key or ADC mode')
        if custom and args.antigravity_adc:
            raise WeaverError('Antigravity CLI ADC staging requires an account model slug')
        if args.zed_subagent or args.kiro_model_defaults or args.qoder_settings or any(
            getattr(args, option) is not None for option in
            ('catalog', 'protocol', 'reasoning_budget', 'reasoning_mode', 'batch_endpoint', 'qoder_catalog')
        ) or (not custom and (args.reasoning is not None or args.context_size is not None)):
            raise WeaverError('Antigravity CLI staging accepts an exact model slug and matching effort')
        artifacts = render_antigravity_cli_model(
            args.model, args.reasoning_effort, custom_alias=args.antigravity_custom_model,
            reasoning_enabled=_reasoning(args), context_size=args.context_size,
            gemini_api=args.provider == 'google' and not args.antigravity_adc,
        )
        if args.antigravity_adc:
            artifacts += render_antigravity_adc(surface)
    elif args.cmd == 'model' and (spec.id, surface) == ('qoder', 'cli'):
        from .qoder_models import render_qoder_cli_model

        if args.zed_subagent or args.kiro_model_defaults or any(
            getattr(args, option) is not None for option in
            ('provider', 'catalog', 'protocol', 'reasoning', 'batch_endpoint')
        ):
            raise WeaverError('Qoder CLI model staging uses a Qoder model ID and native reasoning and context controls')
        artifacts = render_qoder_cli_model(args.model, args.reasoning_effort, args.context_size,
                                           persistent=args.qoder_settings, catalog=args.qoder_catalog,
                                           thinking_mode=args.reasoning_mode, thinking_budget=args.reasoning_budget)
    elif args.cmd == 'model' and args.lmstudio_generator:
        from .lmstudio_generator import render_lmstudio_generator

        if args.provider is None:
            raise WeaverError('LM Studio Generator staging requires an explicit provider')
        if args.qoder_settings or args.kiro_model_defaults or any(value is not None for value in (
            args.reasoning_mode, args.context_size, args.batch_endpoint, args.openrouter_provider_tag,
            args.openrouter_provider_endpoints, args.openrouter_turn_effort,
        )):
            raise WeaverError('LM Studio Generator staging supports Chat Completions model and reasoning controls')
        artifacts = render_lmstudio_generator(
            root, args.provider, args.model, _model_snapshot(args, root), protocol=args.protocol,
            effort=args.reasoning_effort, budget=args.reasoning_budget, reasoning_enabled=_reasoning(args),
        )
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
                                           zed_subagent=getattr(args, 'zed_subagent', False),
                                           zed_profile=getattr(args, 'zed_profile', None),
                                           zed_speed=getattr(args, 'zed_speed', None),
                                           perplexity_fallbacks=getattr(args, 'perplexity_fallback_model', None),
                                           perplexity_max_output_tokens=getattr(args, 'perplexity_max_output_tokens', None),
                                           perplexity_max_steps=getattr(args, 'perplexity_max_steps', None),
                                           perplexity_service_tier=getattr(args, 'perplexity_service_tier', None),
                                           perplexity_effort_preset=getattr(args, 'perplexity_effort_preset', None),
                                           perplexity_profile=getattr(args, 'perplexity_profile', None),
                                           perplexity_profile_version=getattr(args, 'perplexity_profile_version', None),
                                           openrouter_provider_tag=getattr(args, 'openrouter_provider_tag', None),
                                           openrouter_provider_endpoints=getattr(args, 'openrouter_provider_endpoints', None),
                                           openrouter_turn_effort=getattr(args, 'openrouter_turn_effort', None))
            if orchestrator_tools:
                artifacts = _compose_orchestrator_tools(args, root, artifacts)
            elif perplexity_request:
                artifacts = _compose_perplexity_request(args, root, spec, surface, artifacts)
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
            effort=args.reasoning_effort, reasoning_budget=args.reasoning_budget,
            provider_id=args.provider, snapshot=_model_snapshot(args, root),
            llm_profile=args.llm_profile, codex_catalog=args.codex_catalog,
            codex_reasoning_summary=args.codex_reasoning_summary, codex_verbosity=args.codex_verbosity,
            copilot_catalog=args.copilot_catalog, copilot_fallbacks=args.copilot_fallback_model,
            copilot_model_policy=args.copilot_model_policy,
            opencode_catalog=args.opencode_catalog, model_variant=args.model_variant,
            kilo_catalog=args.kilo_catalog,
            factory_catalog=args.factory_catalog,
            qoder_catalog=args.qoder_catalog, claude_catalog=args.claude_catalog,
            cursor_catalog=args.cursor_catalog, cursor_parameters=args.cursor_param, cursor_variant=args.cursor_variant,
            qwen_auth_type=args.qwen_auth_type,
            chatgpt_workspace_agent=args.chatgpt_workspace_agent,
            allowed_skills=args.allow_skill, allowed_mcp=args.allow_mcp, allowed_mcp_tools=args.allow_mcp_tool,
            hermes_profile=args.hermes_profile, profile_skills=args.profile_skill,
            hermes_profile_route=args.hermes_profile_route,
            mcp_profile=args.mcp_profile, profile_mcp=args.profile_mcp,
            profile_context_size=args.profile_context_size,
        )
    else:
        raise WeaverError('Unsupported command')
    handoff = _handoff(spec, surface, target_os, host_os, mcp=args.cmd == 'render',
                      label={'provider': 'PROVIDER-OS', 'model': 'MODEL-OS', 'agent': 'AGENT-OS'}.get(args.cmd),
                      opencode_v2=args.cmd == 'render' and args.opencode_v2,
                      copilot_portable=args.cmd == 'render' and args.copilot_portable,
                      antigravity_user_mcp=args.cmd == 'render' and args.antigravity_user_mcp,
                      devin_stage_disabled=args.cmd == 'render' and args.devin_stage_disabled,
                      lmstudio_responses=args.cmd == 'render' and args.lmstudio_responses,
                      lmstudio_plugins=args.cmd == 'render' and args.lmstudio_mcp_plugins)
    if args.cmd == 'model' and args.lmstudio_generator:
        plugin = next(item for item in artifacts if item.kind == 'plugin-manifest')
        handoff = replace(handoff, relative_path=plugin.relative_path.parent / handoff.relative_path.name)
    artifacts.append(handoff)
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
        if args.cmd == 'cursor-models':
            from .cursor_models import inspect_cursor_models

            result = inspect_cursor_models(args.input, model=args.model, effort=args.reasoning_effort,
                                           parameters=args.cursor_param, variant=args.cursor_variant)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'devin-models':
            from .devin_models import inspect_devin_models

            result = inspect_devin_models(args.input, model=args.model)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'bionic-models':
            from .bionic_models import inspect_bionic_models

            result = inspect_bionic_models(args.input, model=args.model, source=args.source, effort=args.reasoning_effort)
            print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        if args.cmd == 'devin-airgap-models':
            from .devin_models import render_devin_airgap_models

            _emit(args, render_devin_airgap_models(args.input))
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
        if args.cmd in {'opencode-models', 'kilo-models'}:
            from .opencode_models import project_kilo_catalog, project_opencode_catalog

            project_catalog = project_kilo_catalog if args.cmd == 'kilo-models' else project_opencode_catalog
            result = project_catalog(args.input)
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
