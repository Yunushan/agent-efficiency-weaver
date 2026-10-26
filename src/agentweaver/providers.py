from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, NoReturn

from .artifacts import RenderedArtifact
from .config import WeaverError

if TYPE_CHECKING:
    from .integrations import IntegrationSpec

CHECKED_ON = "2026-10-22"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_ENDPOINT = "/chat/completions"
DEFAULT_SECRET_ENV = "OPENROUTER_API_KEY"
CURSOR_ENDPOINT = "https://openrouter.ai/api/v1/cursor"
CLAUDE_ENDPOINT = "https://openrouter.ai/api"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MODEL_RE = re.compile(
    r"^~?[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:+-]*$"
)
_GAP_AGENTS = frozenset({"kiro", "antigravity", "devin"})
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "header",
        "headers",
        "key",
        "token",
    }
)


@dataclass(frozen=True)
class _Metadata:
    base_url: str
    endpoint: str
    secret_env: str


@dataclass(frozen=True)
class _Route:
    allowed_surfaces: tuple[str, ...] | None
    model_prefix: str
    model_guidance: str
    steps: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    credential_note: str
    endpoint: str | None
    endpoint_note: str
    restrictions: tuple[str, ...]
    sources: tuple[str, ...]
    native_toml: bool = False


def _fail(message: str) -> NoReturn:
    raise WeaverError(message)


def _agent_id(agent: IntegrationSpec) -> str:
    value = getattr(agent, "id", None)
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("Invalid integration id")
    return value


def _agent_name(agent: IntegrationSpec, agent_id: str) -> str:
    value = getattr(agent, "name", None)
    if value is None:
        return agent_id
    if not isinstance(value, str):
        _fail("Invalid integration name")
    value = value.strip()
    if not value or "\n" in value or "\r" in value:
        _fail("Invalid integration name")
    if any(
        unicodedata.category(character) in {"Cc", "Cs", "Cf"} for character in value
    ):
        _fail("Invalid integration name")
    return value


def _surface_ids(agent: IntegrationSpec) -> tuple[str, ...]:
    surfaces = getattr(agent, "surfaces", None)
    if isinstance(surfaces, Mapping):
        entries: list[Any] = list(surfaces.values())
    elif isinstance(surfaces, (list, tuple)):
        entries = list(surfaces)
    else:
        _fail("Invalid integration surfaces")
    if not entries:
        _fail("Invalid integration surfaces")
    result: list[str] = []
    for surface in entries:
        value = getattr(surface, "id", None)
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            _fail("Invalid integration surface")
        result.append(value)
    if len(result) != len(set(result)):
        _fail("Invalid integration surfaces")
    return tuple(result)


def _surface(surface: str, available: tuple[str, ...]) -> str:
    if not isinstance(surface, str) or not _ID_RE.fullmatch(surface):
        _fail("Invalid surface")
    if surface not in available:
        _fail("No documented OpenRouter provider route for this surface")
    return surface


def _provider(provider_id: str) -> str:
    if provider_id != "openrouter":
        _fail("Unsupported provider")
    return provider_id


def _model(model_id: str) -> str:
    if not isinstance(model_id, str) or not model_id or len(model_id) > 200:
        _fail("Invalid model id")
    if model_id != model_id.strip():
        _fail("Invalid model id")
    if any(
        unicodedata.category(character) in {"Cc", "Cs", "Cf"} for character in model_id
    ):
        _fail("Invalid model id")
    if any(character in model_id for character in "?#;&=,<>\"'\\ \t"):
        _fail("Invalid model id")
    lowered = model_id.casefold()
    if (
        lowered.startswith(("sk-", "bearer "))
        or "sk-or-" in lowered
        or "sk-ant-" in lowered
        or "sk-proj-" in lowered
    ):
        _fail("Invalid model id")
    if not _MODEL_RE.fullmatch(model_id):
        _fail("Invalid model id")
    return model_id


def _metadata(provider_metadata: Mapping[str, Any] | None) -> _Metadata:
    if provider_metadata is None:
        return _Metadata(DEFAULT_BASE_URL, DEFAULT_ENDPOINT, DEFAULT_SECRET_ENV)
    if not isinstance(provider_metadata, Mapping):
        _fail("Invalid provider metadata")
    for key in provider_metadata:
        if not isinstance(key, str) or key.casefold() in _FORBIDDEN_METADATA_KEYS:
            _fail("Invalid provider metadata")
    base_url = provider_metadata.get("base_url", DEFAULT_BASE_URL)
    endpoint = provider_metadata.get("endpoint", DEFAULT_ENDPOINT)
    secret_env = provider_metadata.get("secret_env", DEFAULT_SECRET_ENV)
    if base_url not in {DEFAULT_BASE_URL, CLAUDE_ENDPOINT}:
        _fail("Invalid provider metadata")
    if endpoint not in {DEFAULT_ENDPOINT, "/api/v1/cursor", "/api"}:
        _fail("Invalid provider metadata")
    if secret_env != DEFAULT_SECRET_ENV:
        _fail("Invalid provider metadata")
    return _Metadata(base_url, endpoint, secret_env)


def _route(agent_id: str, surface: str) -> _Route:
    if agent_id in _GAP_AGENTS:
        _fail(f"No documented OpenRouter provider route for {agent_id}; documented gap")
    route = _ROUTES.get(agent_id)
    if route is None:
        _fail(f"No documented OpenRouter provider route for {agent_id}; documented gap")
    allowed = route.allowed_surfaces
    if allowed is not None and surface not in allowed:
        if agent_id == "qoder":
            _fail(
                "Qoder OpenRouter provider setup is documented for the IDE surface only; "
                "the CLI surface is a documented gap"
            )
        _fail(f"No documented OpenRouter provider route for {agent_id} surface {surface}")
    return route


def _standard_endpoint(metadata: _Metadata) -> str:
    endpoint = metadata.endpoint if metadata.endpoint == DEFAULT_ENDPOINT else DEFAULT_ENDPOINT
    return metadata.base_url.rstrip("/") + endpoint


def _model_text(route: _Route, model_id: str) -> str:
    return route.model_prefix + model_id


def _markdown(
    agent: IntegrationSpec,
    agent_id: str,
    surface: str,
    model_id: str,
    route: _Route,
    metadata: _Metadata,
) -> str:
    name = _agent_name(agent, agent_id)
    rendered_model = _model_text(route, model_id)
    endpoint = route.endpoint or _standard_endpoint(metadata)
    lines = [
        f"# OpenRouter provider setup: {name}",
        "",
        "Staging handoff. This artifact contains no API key and no credential value.",
        "",
        f"- Agent: `{agent_id}`",
        f"- Surface: `{surface}`",
        "- Artifact: `provider-setup.md`",
        "- Provider: `openrouter`",
        f"- OpenRouter registry model: `{model_id}`",
        f"- Model to use on this surface: `{rendered_model}`",
        f"- Endpoint: `{endpoint}`",
        "",
        "## Setup",
        "",
    ]
    for index, step in enumerate(route.steps, 1):
        lines.append(f"{index}. {step.replace('{model}', rendered_model)}")
    lines.extend(
        [
            "",
            "## Model convention",
            "",
            route.model_guidance.replace("{model}", rendered_model),
            "",
            "## Credentials",
            "",
            route.credential_note,
            "",
        ]
    )
    if route.env:
        lines.append("Documented environment variables:")
        lines.append("")
        for env_name, purpose in route.env:
            lines.append(f"- `{env_name}`: {purpose}")
    else:
        lines.append(
            "No OpenRouter-specific environment variable is documented for this surface."
        )
    lines.extend(
        [
            "",
            "Never pass an API key as a command-line argument and never write one into this repository.",
            "",
            "## Endpoint notes",
            "",
            route.endpoint_note,
            "",
            "## Restrictions",
            "",
        ]
    )
    for restriction in route.restrictions:
        lines.append(f"- {restriction}")
    if route.native_toml:
        lines.extend(
            [
                "",
                "A native TOML fragment is staged at "
                f"`{agent_id}/provider/config.toml`; it contains no credential.",
            ]
        )
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Checked on: {'2026-10-26' if agent_id in _EXPANDED_ROUTE_IDS else CHECKED_ON}",
            "- Sources:",
        ]
    )
    for source in route.sources:
        lines.append(f"  - {source}")
    return "\n".join(lines) + "\n"


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml(model_id: str) -> str:
    return "\n".join(
        [
            f"model = {_toml_string(model_id)}",
            'model_provider = "openrouter"',
            "",
            "[model_providers.openrouter]",
            'name = "OpenRouter"',
            f"base_url = {_toml_string(DEFAULT_BASE_URL)}",
            f"env_key = {_toml_string(DEFAULT_SECRET_ENV)}",
            "",
        ]
    )


def provider_evidence(agent_id: str, surface: str) -> dict[str, Any]:
    if not isinstance(agent_id, str) or not _ID_RE.fullmatch(agent_id):
        _fail("Invalid integration id")
    if not isinstance(surface, str) or not _ID_RE.fullmatch(surface):
        _fail("Invalid surface")
    if agent_id == "openrouter":
        _fail("OpenRouter is a provider gateway, not an agent")
    route = _route(agent_id, surface)
    return {
        "state": "available" if route.native_toml else "manual",
        "sources": list(route.sources),
        "checked_on": "2026-10-26" if agent_id in _EXPANDED_ROUTE_IDS else CHECKED_ON,
        "fixture_tests": ["tests/test_expanded_providers.py" if agent_id in _EXPANDED_ROUTE_IDS else "tests/test_providers.py"],
    }


def render_provider_setup(
    agent: IntegrationSpec,
    surface: str,
    *,
    provider_id: str,
    model_id: str,
    provider_metadata: Mapping[str, Any] | None = None,
) -> list[RenderedArtifact]:
    agent_id = _agent_id(agent)
    if getattr(agent, "kind", None) != "agent" or agent_id == "openrouter":
        _fail("OpenRouter is a provider gateway, not an agent; no agent setup can be rendered")
    _provider(provider_id)
    model = _model(model_id)
    available = _surface_ids(agent)
    selected_surface = _surface(surface, available)
    route = _route(agent_id, selected_surface)
    metadata = _metadata(provider_metadata)
    markdown = _markdown(
        agent, agent_id, selected_surface, model, route, metadata
    ).encode("utf-8")
    artifacts = [
        RenderedArtifact(
            PurePosixPath(f"{agent_id}/provider/provider-setup.md"),
            markdown,
            "provider-setup",
        )
    ]
    if route.native_toml:
        artifacts.append(
            RenderedArtifact(
                PurePosixPath(f"{agent_id}/provider/config.toml"),
                _toml(model).encode("utf-8"),
                "provider-config",
            )
        )
    return artifacts


_OPENROUTER = "https://openrouter.ai/docs/quickstart"
_EXPANDED_ROUTE_IDS = frozenset({"factory", "hermes", "qwen-code", "kimi-code"})
_ROUTES = {
    "kilo": _Route(
        None,
        "",
        "Kilo documents the raw OpenRouter model ID as the key under "
        "`provider.openrouter.models` in `kilo.json`. Do not add an `openrouter/` "
        "prefix inside that provider block.",
        (
            "Open Kilo Code Settings and select the Providers tab.",
            "Choose OpenRouter and enter the key in the product settings interface.",
            "Choose the model and keep the raw OpenRouter slug under "
            "`provider.openrouter.models` when editing `kilo.json`.",
        ),
        (),
        "Kilo docs state the extension stores the key in `kilo.json`. This is not "
        "documented as an OS keychain; Weaver must not write the key or treat that "
        "file as secure storage.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        (
            "No OpenRouter-specific environment variable is documented for this surface.",
            "Do not describe the `kilo.json` key location as keychain-backed.",
        ),
        (
            "https://kilo.ai/docs/ai-providers/openrouter",
            "https://kilo.ai/docs/getting-started/setup-authentication",
            _OPENROUTER,
        ),
    ),
    "opencode": _Route(
        ("cli",),
        "openrouter/",
        "OpenCode documents raw slugs as keys under `provider.openrouter.models`; "
        "a top-level default model uses `openrouter/<raw>`.",
        (
            "Run `/connect`, choose OpenRouter, and enter the key in the interactive flow.",
            "Run `/models` and select the model, or add it under "
            "`provider.openrouter.models` in `opencode.json`.",
            "For a default, set the top-level `model` key to `openrouter/<raw>`.",
        ),
        (),
        "OpenCode docs store `/connect` credentials in `auth.json` or the XDG data "
        "directory. This is not documented as an OS keychain; do not stage `auth.json`.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("No OpenRouter-specific environment variable is documented for this surface.",),
        (
            "https://openrouter.ai/docs/cookbook/coding-agents/opencode-integration",
            "https://opencode.ai/docs/providers/",
            _OPENROUTER,
        ),
    ),
    "cline": _Route(
        ("ide",),
        "",
        "Select the model from Cline's provider model dropdown. No prefix convention "
        "is documented for this route.",
        (
            "Open Cline Settings and choose OpenRouter as the API Provider.",
            "Paste the key into the OpenRouter API Key field.",
            "Select the model from the Model dropdown.",
        ),
        (),
        "Enter the key in Cline's API key field. No OpenRouter environment variable or "
        "project config key is documented for this route.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("Use the editor UI for credentials; do not invent a project config file.",),
        (
            "https://docs.cline.bot/provider-config/openrouter",
            _OPENROUTER,
        ),
    ),
    "copilot": _Route(
        ("vscode",),
        "",
        "Add the model through the BYOK model picker and use the identifier shown by "
        "the provider. No prefix convention is documented for this route.",
        (
            "Open Chat: Manage Language Models.",
            "Add a model, choose OpenRouter, and enter the BYOK key in the editor UI.",
            "Select the added model in the chat model picker.",
        ),
        (),
        "Use the VS Code BYOK UI. No OpenRouter environment variable or config key is "
        "documented for this route.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("BYOK coverage is limited to the documented VS Code Chat surface.",),
        (
            "https://code.visualstudio.com/blogs/2026/06/18/byok-vscode",
            "https://code.visualstudio.com/docs/agent-customization/language-models",
        ),
    ),
    "cursor": _Route(
        ("ide",),
        "",
        "Enter the raw OpenRouter model ID when adding a model. Do not add an "
        "`openrouter/` prefix.",
        (
            "Open Cursor Settings > Models > API Keys.",
            "Enable OpenAI API Key and paste the OpenRouter key.",
            f"Enable Override OpenAI Base URL and set it to `{CURSOR_ENDPOINT}`.",
            "Add the raw OpenRouter model ID with + Add model and select it.",
        ),
        (),
        "Enter the key in Cursor Settings > Models > API Keys. No OpenRouter "
        "environment variable is documented for this route.",
        CURSOR_ENDPOINT,
        "The dedicated `/cursor` endpoint is required. The generic OpenRouter "
        "`/api/v1` endpoint does not normalize Cursor's tool format. This OpenRouter "
        "integration is beta.",
        (
            "Tab completions and some Auto or Composer modes are not covered by BYOK.",
            "The base URL must use the dedicated endpoint, not `/api/v1` alone.",
        ),
        ("https://openrouter.ai/docs/cookbook/coding-agents/cursor-integration",),
    ),
    "openhands": _Route(
        ("cli",),
        "openrouter/",
        "Use `openrouter/<raw>` when a model is not in the selector and must be "
        "entered in the Advanced tab.",
        (
            "Open Settings > LLM and select OpenRouter as the provider.",
            "Select a model and enter the key in the settings profile.",
            "For an unlisted model, use Advanced and enter `openrouter/<raw>`.",
        ),
        (),
        "Enter the key in Settings > LLM. The OpenRouter route page documents no "
        "OpenRouter-specific environment variable.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("The model prefix is `openrouter/<raw>` for unlisted model IDs.",),
        (
            "https://docs.openhands.dev/openhands/usage/llms/openrouter",
            _OPENROUTER,
        ),
    ),
    "zed": _Route(
        ("ide",),
        "openrouter/",
        "Set `agent.default_model.model` to `openrouter/<raw>`. Custom entries under "
        "`language_models.open_router.available_models` use the raw slug in `name`.",
        (
            "Open Settings > AI > LLM Providers and enter the key in the OpenRouter row.",
            "Set `agent.default_model.provider` to `openrouter` and "
            "`agent.default_model.model` to `openrouter/<raw>`.",
            "For a custom model, use the raw slug in "
            "`language_models.open_router.available_models[].name`.",
        ),
        (("OPENROUTER_API_KEY", "Read from the local Zed process environment."),),
        "The key can be entered in Settings > AI > LLM Providers or supplied through "
        "`OPENROUTER_API_KEY` in the Zed process environment.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("An OpenRouter assistant requires an explicitly selected model.",),
        (
            "https://zed.dev/docs/ai/use-a-gateway",
            _OPENROUTER,
        ),
    ),
    "junie": _Route(
        ("cli",),
        "",
        "Choose the model with `/model`. No prefix convention is documented for this "
        "route.",
        (
            "Run Junie CLI and open `/account`.",
            "Turn on Use your own API key, select OpenRouter, and enter the key.",
            "Run `/model` and choose the model.",
            "In automation, set `JUNIE_OPENROUTER_API_KEY` in the process environment.",
        ),
        (("JUNIE_OPENROUTER_API_KEY", "Documented Junie CLI OpenRouter key variable."),),
        "Use the documented Junie environment variable. Do not pass the key as a "
        "command-line argument.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("The documented route is the Junie CLI surface.",),
        (
            "https://junie.jetbrains.com/docs/byok-openrouter.html",
            "https://openrouter.ai/docs/cookbook/coding-agents/junie",
            _OPENROUTER,
        ),
    ),
    "goose": _Route(
        ("cli",),
        "",
        "`GOOSE_MODEL` accepts the provider model name. No `openrouter/` prefix is "
        "documented for this route.",
        (
            "Run `goose configure` and select OpenRouter.",
            "Enter the key in the configuration flow, or set `OPENROUTER_API_KEY` in "
            "the process environment.",
            "Set `GOOSE_PROVIDER=openrouter` and `GOOSE_MODEL=<raw model>`.",
        ),
        (
            ("GOOSE_PROVIDER", "Must be `openrouter` for this route."),
            ("GOOSE_MODEL", "Provider model name; no documented prefix."),
            ("OPENROUTER_API_KEY", "Documented OpenRouter key variable."),
        ),
        "Use the documented goose environment variables or configuration flow. Do not "
        "write a key into a staged file.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("No automatic model selection is documented.",),
        (
            "https://goose-docs.ai/docs/getting-started/providers",
            "https://goose-docs.ai/docs/guides/environment-variables/",
            _OPENROUTER,
        ),
    ),
    "pi": _Route(
        ("cli",),
        "openrouter/",
        "Use `openrouter/<raw>` as the model ID.",
        (
            "Run `/login openrouter` and complete the PKCE flow, or set "
            "`OPENROUTER_API_KEY` in the process environment.",
            "Select `openrouter/<raw>` as the model.",
        ),
        (("OPENROUTER_API_KEY", "Documented Pi OpenRouter key variable."),),
        "Use `/login openrouter` or `OPENROUTER_API_KEY`. Do not write the credential "
        "into a staged artifact.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("Model IDs use the `openrouter/` prefix.",),
        (
            "https://pi.dev/docs/latest/providers.md",
            _OPENROUTER,
        ),
    ),
    "codex": _Route(
        ("cli", "desktop", "ide"),
        "",
        "Use the raw OpenRouter model ID for `model` in `config.toml`; the provider "
        "block supplies OpenRouter.",
        (
            "Place the staged `config.toml` fragment in the documented Codex config "
            "scope, or merge the equivalent keys.",
            "Set `OPENROUTER_API_KEY` in the process environment; `env_key` tells Codex "
            "which variable to read.",
            "Do not put the key in the TOML file.",
        ),
        (("OPENROUTER_API_KEY", "Read by Codex through `env_key`."),),
        "Use `env_key` and the process environment. Never write the key into the TOML "
        "fragment.",
        None,
        "`base_url` is the documented OpenRouter OpenAI-compatible endpoint.",
        (
            "The native TOML fragment contains no credential.",
            "Use an explicit raw OpenRouter model ID; no model is chosen automatically.",
        ),
        (
            "https://openrouter.ai/docs/cookbook/coding-agents/codex-cli",
            "https://developers.openai.com/codex/config-reference",
            _OPENROUTER,
        ),
        True,
    ),
    "abacus": _Route(
        ("desktop",),
        "",
        "Add `providers.openrouter` with the documented base URL and "
        "`apiKeyEnvVar`, then choose the model with `/model`. No OpenRouter-specific "
        "top-level model string is documented.",
        (
            "Run `/providers` and add an OpenAI-compatible provider named `openrouter`.",
            f"Set the base URL to `{DEFAULT_BASE_URL}` and `apiKeyEnvVar` to "
            "`OPENROUTER_API_KEY`.",
            "Choose the model with `/model`.",
        ),
        (("OPENROUTER_API_KEY", "Documented `apiKeyEnvVar` value for the provider."),),
        "Abacus reads the key from the documented `apiKeyEnvVar`; do not paste a key "
        "into the staged config.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("No OpenRouter-specific model prefix is documented.",),
        (
            "https://abacus.ai/help/abacusai-desktop/providers",
            _OPENROUTER,
        ),
    ),
    "qoder": _Route(
        ("ide",),
        "",
        "Choose the preset OpenRouter provider and enter the model ID. No prefix "
        "convention is documented.",
        (
            "Open Qoder IDE Settings > Models.",
            "Click + Add and choose the preset provider OpenRouter.",
            "Enter the key and at least one model ID, then select the model in chat.",
        ),
        (),
        "Enter the key in Qoder IDE Settings > Models. No OpenRouter environment "
        "variable is documented for this route.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        (
            "This documented route is IDE only; the CLI route is not inferred.",
            "Repo Wiki uses a fixed model and is billed separately.",
        ),
        ("https://docs.qoder.com/qoder/custom-models", _OPENROUTER),
    ),
    "amp": _Route(
        ("cli",),
        "",
        "Use the raw OpenRouter slug in model mapping. No prefix convention is "
        "documented for this route.",
        (
            "Open Amp Model Routing settings.",
            "Add an AI model router or gateway connection and choose OpenRouter.",
            "Use the raw slug in model mapping and manage the connection with "
            "`amp config model-providers`.",
        ),
        (),
        "Amp stores the connection credential. No OpenRouter environment variable is "
        "documented for this route.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("Model mappings use the raw OpenRouter slug.",),
        ("https://ampcode.com/docs/customize/model-routing", _OPENROUTER),
    ),
    "claude-code": _Route(
        ("cli",),
        "openrouter/",
        "Use `openrouter/<raw>` in model settings or the gateway model picker.",
        (
            "Set `OPENROUTER_API_KEY` in the process environment.",
            f"Set `ANTHROPIC_BASE_URL={CLAUDE_ENDPOINT}`. Do not append `/v1`.",
            "Set `ANTHROPIC_AUTH_TOKEN` to the OpenRouter key value and "
            "`ANTHROPIC_API_KEY` to an empty string.",
            "Optionally set `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`, then choose "
            "`openrouter/<raw>` with `/model` or a documented model override.",
        ),
        (
            ("OPENROUTER_API_KEY", "Source credential for the gateway route."),
            ("ANTHROPIC_BASE_URL", "Must be the Anthropic skin base URL."),
            ("ANTHROPIC_AUTH_TOKEN", "Bearer token value used by Claude Code."),
            ("ANTHROPIC_API_KEY", "Must be explicitly empty to avoid Anthropic auth."),
            (
                "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
                "Optional gateway model picker.",
            ),
        ),
        "Use environment variables. OpenRouter recommends an OS keychain instead of a "
        "plaintext shell profile; do not write the key into a settings file.",
        CLAUDE_ENDPOINT,
        "Use the Anthropic skin base URL with no `/v1` suffix. The full OpenRouter "
        "OpenAI-compatible endpoint is not this route's base URL.",
        (
            "OpenRouter guarantees this route only with the Anthropic first-party provider.",
            "Anthropic's gateway documentation does not support routing Claude Code to "
            "non-Claude models.",
        ),
        (
            "https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration",
            "https://code.claude.com/docs/en/llm-gateway",
            _OPENROUTER,
        ),
    ),
    "aider": _Route(
        ("cli",),
        "openrouter/",
        "Pass `--model {model}`. Do not choose a model automatically.",
        (
            "Set `OPENROUTER_API_KEY` in the process environment.",
            "Run `aider --model {model}`.",
            "Use `aider --list-models openrouter/` to discover models.",
        ),
        (("OPENROUTER_API_KEY", "Documented Aider OpenRouter key variable."),),
        "Use `OPENROUTER_API_KEY`. Do not pass the key as a command-line argument.",
        None,
        "Use the documented OpenRouter base URL and chat completions endpoint.",
        ("No automatic model selection is documented.",),
        (
            "https://aider.chat/docs/llms/openrouter.html",
            _OPENROUTER,
        ),
    ),
}

# These are deliberate manual handoffs. A documented compatible endpoint does not
# establish installed-version validation, inference success, or native agent export.
_ROUTES.update({
    "factory": _Route(
        allowed_surfaces=("cli",), model_prefix="",
        model_guidance="Use the explicit provider model ID in customModels[].model: `{model}`.",
        steps=(
            "Open `/settings` > Custom models, or `/model` > Manage custom models.",
            "Add a generic-chat-completion-api custom model with the OpenRouter base URL.",
            "Use `{model}` as model and `${OPENROUTER_API_KEY}` as the apiKey environment reference.",
            "Review and merge into `~/.factory/settings.json`; do not replace unrelated customModels.",
        ),
        env=(("OPENROUTER_API_KEY", "Referenced by Factory as ${OPENROUTER_API_KEY}."),),
        credential_note="Factory supports environment references in apiKey; no credential is staged.",
        endpoint=None, endpoint_note="The generic Chat Completions route uses the OpenRouter API base URL.",
        restrictions=("This route is a manual configuration handoff for the registered CLI surface.",
                      "No web/mobile model access or inference was verified."),
        sources=("https://docs.factory.com/model-independence/byok", _OPENROUTER),
    ),
    "hermes": _Route(
        allowed_surfaces=("cli",), model_prefix="",
        model_guidance="Use the explicit gateway model slug `{model}`; do not rely on automatic alias resolution.",
        steps=("Set OPENROUTER_API_KEY in the local Hermes process environment.",
               "Choose the authenticated OpenRouter provider in the model picker or `/model`.",
               "Set model.provider to openrouter and model.default to `{model}` in the reviewed config.yaml model mapping.",
               "New sessions read changed settings; use `/model` to deliberately switch an existing session."),
        env=(("OPENROUTER_API_KEY", "Credential for the authenticated OpenRouter provider."),),
        credential_note="Use the local credential setup; never stage api_key or a dashboard session token.",
        endpoint=None, endpoint_note="Use OpenRouter's documented OpenAI-compatible API endpoint.",
        restrictions=("The picker is curated and does not establish coverage of every model in a raw gateway catalog.",
                      "No dashboard service is enabled by this handoff."),
        sources=("https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models", _OPENROUTER),
    ),
    "qwen-code": _Route(
        allowed_surfaces=("cli",), model_prefix="",
        model_guidance="Place `{model}` under modelProviders.openai[].id with an explicit endpoint and envKey.",
        steps=("Review ~/.qwen/settings.json before changing modelProviders.",
               "Add an OpenAI-compatible route with the OpenRouter baseUrl, envKey OPENROUTER_API_KEY and id `{model}`.",
               "Choose the route using `/model`; provider arrays replace their matching settings scope."),
        env=(("OPENROUTER_API_KEY", "Referenced through modelProviders.openai[].envKey."),),
        credential_note="Keep only the environment variable name in envKey; no key is read or written.",
        endpoint=None, endpoint_note="Explicit OpenAI-compatible Chat Completions route; other protocols are separate routes.",
        restrictions=("An explicit model ID is accepted syntax, not proof of account access or successful inference.",
                      "Reasoning must be supported by the selected model and protocol."),
        sources=("https://qwenlm.github.io/qwen-code-docs/en/users/configuration/model-providers/", _OPENROUTER),
    ),
    "kimi-code": _Route(
        allowed_surfaces=("cli",), model_prefix="",
        model_guidance="Set an explicit upstream model `{model}` in a configured model alias.",
        steps=("Use current Kimi Code ~/.kimi-code/config.toml; legacy ~/.kimi belongs to archived Python Kimi CLI.",
               "Configure an openai provider with the OpenRouter base_url and api_key_env OPENROUTER_API_KEY.",
               "Declare the model alias with `{model}` and a separately documented max_context_size.",
               "Select the alias using `/model`; do not copy OAuth credentials during legacy migration."),
        env=(("OPENROUTER_API_KEY", "Explicitly declared by api_key_env; no automatic secret binding."),),
        credential_note="Use api_key_env instead of a literal api_key in config.toml.",
        endpoint=None, endpoint_note="Custom openai provider selects Chat Completions; openai_responses is separate.",
        restrictions=("The required context limit is not inferred from a model's name.",
                      "This model handoff does not implement a custom-agent model field; current Kimi ignores that field."),
        sources=("https://moonshotai.github.io/kimi-code/en/configuration/providers.html",
                 "https://moonshotai.github.io/kimi-code/en/configuration/config-files.html", _OPENROUTER),
    ),
})
