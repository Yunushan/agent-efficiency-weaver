import os
import socket
import subprocess
import tomllib
import unicodedata
import unittest
import urllib.request
from pathlib import PurePosixPath
from typing import Any
from unittest import mock

from agentweaver.artifacts import RenderedArtifact
from agentweaver.config import WeaverError
from agentweaver.integrations import IntegrationSpec, SurfaceSpec
from agentweaver.providers import provider_evidence, render_provider_setup

CHECKED_ON = "2026-10-22"
MODEL = "anthropic/claude-sonnet-4"

AGENT_SURFACES: dict[str, tuple[str, ...]] = {
    "abacus": ("desktop",),
    "aider": ("cli",),
    "amp": ("cli",),
    "antigravity": ("ide", "app-ui"),
    "claude-code": ("cli",),
    "cline": ("ide",),
    "codex": ("cli",),
    "copilot": ("vscode",),
    "cursor": ("ide",),
    "devin": ("desktop-local", "legacy-cascade"),
    "goose": ("cli",),
    "junie": ("cli",),
    "kilo": ("cli", "ide"),
    "kiro": ("ide", "cli"),
    "opencode": ("cli",),
    "openhands": ("cli",),
    "openrouter": ("gateway",),
    "pi": ("cli",),
    "qoder": ("cli", "ide"),
    "zed": ("ide",),
}

def unchecked(value: Any) -> Any:
    return value


def spec(
    agent_id: str,
    *,
    kind: str = "agent",
    name: str | None = None,
    surfaces: tuple[str, ...] | None = None,
) -> IntegrationSpec:
    surface_ids = surfaces if surfaces is not None else AGENT_SURFACES.get(agent_id, ("cli",))
    surface_map: dict[str, SurfaceSpec] = {}
    for surface_id in surface_ids:
        surface = object.__new__(SurfaceSpec)
        object.__setattr__(surface, "id", surface_id)
        surface_map[surface_id] = surface
    integration = object.__new__(IntegrationSpec)
    object.__setattr__(integration, "id", agent_id)
    object.__setattr__(integration, "name", name or agent_id)
    object.__setattr__(integration, "kind", kind)
    object.__setattr__(integration, "default_surface", surface_ids[0])
    object.__setattr__(integration, "surfaces", surface_map)
    return integration


def render(
    agent_id: str,
    surface: str = "cli",
    model: str = MODEL,
    **kwargs: Any,
) -> list[RenderedArtifact]:
    return render_provider_setup(
        spec(agent_id),
        surface,
        provider_id="openrouter",
        model_id=model,
        **kwargs,
    )


def markdown(artifacts: list[RenderedArtifact]) -> RenderedArtifact | None:
    items = [item for item in artifacts if item.kind == "provider-setup"]
    return items[0] if items else None


def setup_text(artifacts: list[RenderedArtifact]) -> str:
    item = markdown(artifacts)
    if item is None:
        raise AssertionError("missing provider-setup artifact")
    return item.content.decode("utf-8")


class ProviderRouteTests(unittest.TestCase):
    CASES: tuple[tuple[str, str, str, str], ...] = (
        ("kilo", "ide", "provider.openrouter.models", "https://kilo.ai/docs/ai-providers/openrouter"),
        ("kilo", "cli", "provider.openrouter.models", "https://kilo.ai/docs/getting-started/setup-authentication"),
        ("opencode", "cli", "openrouter/anthropic/claude-sonnet-4", "https://openrouter.ai/docs/cookbook/coding-agents/opencode-integration"),
        ("cline", "ide", "Model", "https://docs.cline.bot/provider-config/openrouter"),
        ("copilot", "vscode", "OpenRouter", "https://code.visualstudio.com/blogs/2026/06/18/byok-vscode"),
        ("cursor", "ide", "https://openrouter.ai/api/v1/cursor", "https://openrouter.ai/docs/cookbook/coding-agents/cursor-integration"),
        ("openhands", "cli", "openrouter/anthropic/claude-sonnet-4", "https://docs.openhands.dev/openhands/usage/llms/openrouter"),
        ("zed", "ide", "openrouter/anthropic/claude-sonnet-4", "https://zed.dev/docs/ai/use-a-gateway"),
        ("junie", "cli", "JUNIE_OPENROUTER_API_KEY", "https://junie.jetbrains.com/docs/byok-openrouter.html"),
        ("goose", "cli", "GOOSE_PROVIDER", "https://goose-docs.ai/docs/getting-started/providers"),
        ("pi", "cli", "openrouter/anthropic/claude-sonnet-4", "https://pi.dev/docs/latest/providers.md"),
        ("codex", "cli", "OPENROUTER_API_KEY", "https://openrouter.ai/docs/cookbook/coding-agents/codex-cli"),
        ("abacus", "desktop", "OPENROUTER_API_KEY", "https://abacus.ai/help/abacusai-desktop/providers"),
        ("qoder", "ide", "OpenRouter", "https://docs.qoder.com/qoder/custom-models"),
        ("amp", "cli", "OpenRouter", "https://ampcode.com/docs/customize/model-routing"),
        ("claude-code", "cli", "https://openrouter.ai/api", "https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration"),
        ("aider", "cli", "openrouter/anthropic/claude-sonnet-4", "https://aider.chat/docs/llms/openrouter.html"),
    )

    def test_documented_routes_render_markdown_handoff_with_evidence(self) -> None:
        for agent_id, surface, expected_model_text, source in self.CASES:
            with self.subTest(agent_id=agent_id, surface=surface):
                artifacts = render(agent_id, surface)
                item = markdown(artifacts)
                self.assertIsNotNone(item)
                assert item is not None
                self.assertEqual(
                    item.relative_path,
                    PurePosixPath(f"{agent_id}/provider/provider-setup.md"),
                )
                text = item.content.decode("utf-8")
                self.assertIn("Staging handoff", text)
                self.assertIn(expected_model_text, text)
                self.assertIn(source, text)
                self.assertIn("2026-10-26" if agent_id == "codex" else CHECKED_ON, text)
                self.assertNotIn("\r", text)

    def test_accepts_native_integration_spec_surface_mapping(self) -> None:
        surfaces: dict[str, SurfaceSpec] = {}
        surface = object.__new__(SurfaceSpec)
        object.__setattr__(surface, "id", "cli")
        surfaces["cli"] = surface
        integration = object.__new__(IntegrationSpec)
        object.__setattr__(integration, "id", "aider")
        object.__setattr__(integration, "name", "Aider")
        object.__setattr__(integration, "kind", "agent")
        object.__setattr__(integration, "default_surface", "cli")
        object.__setattr__(integration, "surfaces", surfaces)
        artifacts = render_provider_setup(integration, "cli", provider_id="openrouter", model_id=MODEL)
        self.assertIn("openrouter/anthropic/claude-sonnet-4", setup_text(artifacts))

    def test_aider_adds_documented_openrouter_prefix_without_choosing_a_model(self) -> None:
        text = setup_text(render("aider", "cli", model="anthropic/claude-3.7-sonnet"))
        self.assertIn("--model openrouter/anthropic/claude-3.7-sonnet", text)
        self.assertNotIn("--model openrouter/openrouter/", text)
        with self.assertRaises(WeaverError):
            render("aider", "cli", model="")

    def test_codex_adds_native_toml_fragment_without_credentials(self) -> None:
        artifacts = render("codex", "cli", model="openai/gpt-5.2")
        self.assertEqual(len(artifacts), 2)
        by_path = {item.relative_path: item for item in artifacts}
        toml_item = by_path[PurePosixPath("codex/provider/config.toml")]
        data = tomllib.loads(toml_item.content.decode("utf-8"))
        self.assertEqual(data["model"], "openai/gpt-5.2")
        self.assertEqual(data["model_provider"], "openrouter")
        provider = data["model_providers"]["openrouter"]
        self.assertEqual(provider["name"], "OpenRouter")
        self.assertEqual(provider["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(provider["env_key"], "OPENROUTER_API_KEY")
        raw = toml_item.content.decode("utf-8")
        self.assertNotIn("api_key", raw)
        self.assertNotIn("sk-or-", raw)
        self.assertIn(
            "codex/provider/config.toml",
            setup_text(artifacts),
        )

    def test_cursor_uses_dedicated_endpoint_and_claude_uses_anthropic_skin(self) -> None:
        cursor = setup_text(render("cursor", "ide"))
        self.assertIn("https://openrouter.ai/api/v1/cursor", cursor)
        self.assertIn("beta", cursor)
        self.assertIn("generic", cursor)
        claude = setup_text(render("claude-code", "cli"))
        self.assertIn("ANTHROPIC_BASE_URL=https://openrouter.ai/api", claude)
        self.assertIn("Do not append `/v1`", claude)
        self.assertIn("non-Claude", claude)
        self.assertIn("does not support", claude)

    def test_kilo_and_opencode_credentials_are_not_claimed_keychain_secure(self) -> None:
        kilo = setup_text(render("kilo", "ide"))
        self.assertIn("kilo.json", kilo)
        self.assertIn("not documented as an OS keychain", kilo)
        opencode = setup_text(render("opencode", "cli"))
        self.assertIn("auth.json", opencode)
        self.assertIn("not documented as an OS keychain", opencode)
        self.assertNotIn("OPENROUTER_API_KEY", kilo)
        self.assertNotIn("OPENROUTER_API_KEY", opencode)

    def test_documented_model_conventions_are_surface_specific(self) -> None:
        opencode = setup_text(render("opencode", "cli"))
        self.assertIn("provider.openrouter.models", opencode)
        self.assertIn("openrouter/anthropic/claude-sonnet-4", opencode)
        zed = setup_text(render("zed", "ide"))
        self.assertIn("agent.default_model.model", zed)
        self.assertIn("openrouter/anthropic/claude-sonnet-4", zed)
        claude = setup_text(render("claude-code", "cli"))
        self.assertIn("openrouter/anthropic/claude-sonnet-4", claude)
        codex = setup_text(render("codex", "cli"))
        self.assertIn("raw OpenRouter model ID", codex)
        self.assertIn("anthropic/claude-sonnet-4", codex)

    def test_metadata_override_is_validated_and_used_when_documented(self) -> None:
        metadata: dict[str, Any] = {
            "base_url": "https://openrouter.ai/api/v1",
            "endpoint": "/chat/completions",
            "secret_env": "OPENROUTER_API_KEY",
            "model_selection": "explicit",
            "model_ids": ["anthropic/claude-sonnet-4"],
            "mcp_service_enabled": False,
        }
        text = setup_text(render("aider", "cli", provider_metadata=metadata))
        self.assertIn("https://openrouter.ai/api/v1/chat/completions", text)
        invalid: tuple[Any, ...] = (
            {"base_url": "https://evil.example/v1"},
            {"base_url": "https://openrouter.ai/api/v1", "endpoint": "/evil"},
            {"base_url": "https://openrouter.ai/api/v1", "secret_env": "OTHER_KEY"},
            {"base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-v1-secret"},
            {"base_url": "https://openrouter.ai/api/v1", "Authorization": "Bearer x"},
            {"base_url": "https://openrouter.ai/api/v1?token=x"},
            [],
        )
        for value in invalid:
            with self.subTest(metadata=value):
                with self.assertRaises(WeaverError):
                    render("aider", "cli", provider_metadata=unchecked(value))


class ProviderEvidenceTests(unittest.TestCase):
    def test_evidence_matches_rendered_sources(self) -> None:
        for agent_id, surface, _, source in ProviderRouteTests.CASES:
            with self.subTest(agent_id=agent_id, surface=surface):
                data = provider_evidence(agent_id, surface)
                self.assertEqual(data["state"], "available" if agent_id == "codex" else "manual")
                self.assertIn(source, data["sources"])
                self.assertEqual(data["checked_on"], "2026-10-26" if agent_id == "codex" else CHECKED_ON)
                self.assertEqual(data["fixture_tests"], ["tests/test_providers.py", "tests/test_expanded_providers.py"]
                                 if agent_id == "codex" else ["tests/test_providers.py"])
                text = setup_text(render(agent_id, surface))
                for url in data["sources"]:
                    self.assertIn(url, text)

    def test_evidence_is_read_only(self) -> None:
        first = provider_evidence("aider", "cli")
        first["sources"].append("https://example.invalid/")
        self.assertNotIn("https://example.invalid/", provider_evidence("aider", "cli")["sources"])

    def test_evidence_rejects_gaps_and_gateway(self) -> None:
        for agent_id, surface in (
            ("kiro", "ide"),
            ("antigravity", "ide"),
            ("devin", "desktop-local"),
            ("qoder", "cli"),
            ("openrouter", "gateway"),
            ("aider", "ide"),
            ("", "cli"),
            ("aider", ""),
        ):
            with self.subTest(agent_id=agent_id, surface=surface):
                with self.assertRaises(WeaverError):
                    provider_evidence(agent_id, surface)


class ProviderGapTests(unittest.TestCase):
    def test_openrouter_provider_cannot_render_as_agent(self) -> None:
        with self.assertRaises(WeaverError) as caught:
            render_provider_setup(
                spec("openrouter", kind="provider"),
                "gateway",
                provider_id="openrouter",
                model_id=MODEL,
            )
        self.assertIn("gateway", str(caught.exception))
        with self.assertRaises(WeaverError):
            render_provider_setup(
                spec("other", kind="provider", surfaces=("gateway",)),
                "gateway",
                provider_id="openrouter",
                model_id=MODEL,
            )

    def test_documented_gaps_are_explicit(self) -> None:
        for agent_id, surface in (
            ("kiro", "ide"),
            ("kiro", "cli"),
            ("antigravity", "ide"),
            ("antigravity", "app-ui"),
            ("devin", "desktop-local"),
            ("devin", "legacy-cascade"),
        ):
            with self.subTest(agent_id=agent_id, surface=surface):
                with self.assertRaises(WeaverError) as caught:
                    render(agent_id, surface)
                self.assertIn("No documented OpenRouter provider route", str(caught.exception))
                self.assertIn("gap", str(caught.exception))

    def test_qoder_default_cli_is_a_gap_and_ide_is_supported(self) -> None:
        with self.assertRaises(WeaverError) as caught:
            render("qoder", "cli")
        self.assertIn("IDE", str(caught.exception))
        self.assertIn("IDE", setup_text(render("qoder", "ide")))

    def test_unsupported_surfaces_and_agents_fail_clearly(self) -> None:
        with self.assertRaises(WeaverError):
            render("kilo", "desktop")
        with self.assertRaises(WeaverError):
            render("claude-code", "ide")
        with self.assertRaises(WeaverError):
            render("unknown-agent", "cli")
        with self.assertRaises(WeaverError):
            render_provider_setup(spec("aider"), "cli", provider_id="other", model_id=MODEL)


class ProviderValidationTests(unittest.TestCase):
    def test_invalid_model_ids_are_rejected_without_echo(self) -> None:
        values: tuple[Any, ...] = (
            "",
            " ",
            "anthropic/claude\nmalicious",
            "anthropic/claude?x=1",
            "anthropic/claude#fragment",
            "anthropic/claude&x=1",
            "Bearer sk-or-v1-secret",
            "sk-or-v1-secret",
            "anthropic/sk-or-v1-secret",
            "anthropic/claude\x00",
            "anthropic/claude\ud800",
            "anthropic/claude;Header",
            "anthropic/claude=value",
            "anthropic/claude\\x",
            "$HOME/model",
            "anthropic/claude sonnet",
            "https://openrouter.ai/api/v1",
            "openrouter/anthropic/claude",
            None,
            1,
            [],
            {},
            b"anthropic/claude",
        )
        for value in values:
            with self.subTest(value=repr(value)):
                with self.assertRaises(WeaverError) as caught:
                    render("aider", "cli", model=unchecked(value))
                self.assertEqual(str(caught.exception), "Invalid model id")
                self.assertNotIn("sk-or-", str(caught.exception))

    def test_documented_alias_and_variant_ids_are_accepted(self) -> None:
        for value in (
            "anthropic/claude-sonnet-4",
            "~anthropic/claude-opus-latest",
            "deepseek/deepseek-v4-flash",
            "openai/gpt-5.2",
            "moonshotai/kimi-k2",
            "anthropic/claude-sonnet-4:free",
        ):
            with self.subTest(value=value):
                self.assertIn(f"openrouter/{value}", setup_text(render("aider", "cli", model=value)))


class ProviderIsolationTests(unittest.TestCase):
    def test_hostile_environment_and_network_are_not_used(self) -> None:
        secret = "sk-or-v1-HOSTILE-ENVIRONMENT-VALUE"
        hostile = {
            "OPENROUTER_API_KEY": secret,
            "ANTHROPIC_AUTH_TOKEN": secret,
            "HOME": "C:\\hostile-home",
            "USERPROFILE": "C:\\hostile-profile",
        }
        with (
            mock.patch.dict(os.environ, hostile, clear=False),
            mock.patch.object(socket, "socket", side_effect=AssertionError("network")),
            mock.patch.object(subprocess, "run", side_effect=AssertionError("subprocess")),
            mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")),
        ):
            for agent_id, surface, _, _ in ProviderRouteTests.CASES:
                with self.subTest(agent_id=agent_id, surface=surface):
                    artifacts = render(agent_id, surface, model="anthropic/claude-sonnet-4")
                    for item in artifacts:
                        self.assertNotIn(secret.encode(), item.content)
                        self.assertNotIn(b"sk-or-", item.content)
        for char in secret:
            self.assertFalse(unicodedata.category(char).startswith("C"))

    def test_no_key_flags_or_placeholder_interpolation_are_rendered(self) -> None:
        for agent_id, surface, _, _ in ProviderRouteTests.CASES:
            with self.subTest(agent_id=agent_id, surface=surface):
                for item in render(agent_id, surface):
                    text = item.content.decode("utf-8")
                    self.assertNotIn("--api-key", text)
                    self.assertNotIn("--openrouter-api-key", text)
                    self.assertNotIn("${", text)
                    self.assertNotIn("{{", text)
                    self.assertNotIn("YOUR_KEY", text)
                    self.assertNotIn("sk-or-", text)


if __name__ == "__main__":
    unittest.main()
