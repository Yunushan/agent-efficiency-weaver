"""Offline configuration modules for the Google Antigravity Python SDK."""
from pathlib import PurePosixPath

from .artifacts import RenderedArtifact
from .config import WeaverError

SDK_PREFIX = PurePosixPath('antigravity', 'sdk')
SDK_ENDPOINTS = ('gemini', 'vertex', 'ollama', 'lm-studio')
GEMINI_LEVELS = {'minimal': 'minimal', 'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'extra_high'}
LOCAL_URLS = {'ollama': 'http://localhost:11434/v1', 'lm-studio': 'http://localhost:1234/v1'}


def render_sdk_model(model: str, endpoint: str, effort: str | None) -> list[RenderedArtifact]:
    from .models import _model_id, _scan

    _scan(model)
    model = _model_id(model)
    if endpoint not in SDK_ENDPOINTS:
        raise WeaverError('Choose a documented Antigravity SDK endpoint')
    if effort is not None and (endpoint in LOCAL_URLS or effort not in GEMINI_LEVELS):
        raise WeaverError('SDK thinking levels minimal, low, medium, high and xhigh require Gemini or Vertex AI')

    constructor = 'LocalOpenAIAgentConfig' if endpoint in LOCAL_URLS else 'LocalAgentConfig'
    imports = f'from google.antigravity import BuiltinTools, CapabilitiesConfig, {constructor}\n'
    selector = repr(model)
    if endpoint in LOCAL_URLS:
        route = f'        base_url={LOCAL_URLS[endpoint]!r},\n'
    else:
        route = f'        vertex={endpoint == "vertex"!r},\n'
        if effort is not None:
            endpoint_type = 'VertexEndpoint' if endpoint == 'vertex' else 'GeminiAPIEndpoint'
            imports += f'from google.antigravity.models import {endpoint_type}, GeminiModelOptions, ModelTarget\n'
            selector = (f'ModelTarget(name={model!r}, endpoint={endpoint_type}(\n'
                        f'            options=GeminiModelOptions(thinking_level={GEMINI_LEVELS[effort]!r}),\n'
                        '        ))')
    source = (
        '"""Build a reviewed SDK configuration without starting an agent."""\n'
        '\n' + imports + '\n\n'
        'def build_config(*, system_instructions=None, skills_paths=(), mcp_servers=(), subagents=()):\n'
        '    tools = [BuiltinTools.VIEW_FILE, BuiltinTools.FINISH]\n'
        '    if subagents:\n'
        '        tools.append(BuiltinTools.INVOKE_SUBAGENT)\n'
        f'    return {constructor}(\n'
        f'        model={selector},\n'
        + route + '        capabilities=CapabilitiesConfig(enabled_tools=tools),\n'
        '        system_instructions=system_instructions,\n'
        '        skills_paths=list(skills_paths),\n'
        '        mcp_servers=list(mcp_servers),\n'
        '        subagents=list(subagents),\n'
        '    )\n'
    )
    setup = {
        'gemini': 'Supply GEMINI_API_KEY separately in the authorized SDK process.',
        'vertex': 'Supply GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION and authorized ADC separately.',
        'ollama': 'Verify the model selector in the approved local Ollama server.',
        'lm-studio': 'Verify the model selector in the approved LM Studio local server.',
    }[endpoint]
    notes = ('# Antigravity SDK model configuration\n\n'
             'Import build_config from model_config.py into your Python application after review. '
             f'{setup} Check model and thinking-level support on the intended endpoint. '
             'This artifact constructs configuration only. The caller owns SDK installation and execution.\n')
    return [RenderedArtifact(SDK_PREFIX / 'model_config.py', source.encode(), 'model-config'),
            RenderedArtifact(SDK_PREFIX / 'MODEL.md', notes.encode(), 'model-handoff')]


def render_sdk_context(instructions: str, skills: list[RenderedArtifact]) -> list[RenderedArtifact]:
    directory = SDK_PREFIX / 'context'
    paths = [skill.relative_path.parent.relative_to(directory).as_posix() for skill in skills]
    source = (
        '"""Load reviewed instructions and explicit Skill directories."""\n'
        'from pathlib import Path\n\n\n'
        'def context_options():\n'
        '    directory = Path(__file__).resolve().parent\n'
        '    return {\n'
        '        "system_instructions": (directory / "INSTRUCTIONS.md").read_text(encoding="utf-8"),\n'
        f'        "skills_paths": [str(directory / path) for path in {paths!r}],\n'
        '    }\n'
    )
    return [RenderedArtifact(directory / 'context_config.py', source.encode(), 'instructions'),
            RenderedArtifact(directory / 'INSTRUCTIONS.md', instructions.encode(), 'instructions'), *skills]


def sdk_subagent(name: str, description: str, instructions: str, model: str | None,
                 skills: tuple[str, ...]) -> bytes:
    skills_type = 'SubagentOverrideSkillsConfig' if skills else 'SubagentNoneSkillsConfig'
    skill_config = f'{skills_type}()'
    paths_import = ''
    if skills:
        paths_import = 'from pathlib import Path\n\n'
        skill_config = (f'{skills_type}(skills_paths=[\n'
                        '            str(Path(__file__).resolve().parent / "skills" / name)\n'
                        f'            for name in {skills!r}\n'
                        '        ])')
    selector = f'        model={model!r},\n' if model is not None else ''
    return (
        '"""Construct a reviewed Antigravity SDK subagent definition."""\n'
        + paths_import + 'from google.antigravity.types import (\n'
        f'    BuiltinTools, SubagentCapabilities, SubagentConfig, {skills_type},\n'
        ')\n\n\n'
        'def build_subagent():\n'
        '    return SubagentConfig(\n'
        f'        name={name!r},\n'
        f'        description={description!r},\n'
        f'        system_instructions={instructions!r},\n'
        + selector + '        tools=[],\n'
        '        capabilities=SubagentCapabilities(enabled_tools=[BuiltinTools.VIEW_FILE, BuiltinTools.FINISH]),\n'
        f'        skills_config={skill_config},\n'
        '    )\n'
    ).encode()
