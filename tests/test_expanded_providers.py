import tomllib
import unittest

from test_providers import setup_text, spec

from agentweaver.config import WeaverError
from agentweaver.providers import provider_evidence, render_provider_setup


class ExpandedProviderTests(unittest.TestCase):
    def test_new_routes_are_manual_scoped_and_credential_free(self) -> None:
        for agent in ("factory", "hermes", "qwen-code", "kimi-code"):
            with self.subTest(agent=agent):
                artifacts = render_provider_setup(spec(agent), "cli", provider_id="openrouter", model_id="vendor/model")
                text = setup_text(artifacts)
                self.assertIn("vendor/model", text)
                self.assertIn("OPENROUTER_API_KEY", text)
                self.assertIn("2026-10-26", text)
                self.assertEqual(len(artifacts), 1)
                evidence = provider_evidence(agent, "cli")
                self.assertEqual(evidence["state"], "manual")
                self.assertEqual(evidence["fixture_tests"], ["tests/test_expanded_providers.py"])

    def test_unresearched_product_or_surface_does_not_inherit_route(self) -> None:
        for agent, surface in (("jetbrains-air", "desktop"), ("chatgpt", "web"), ("qwen-code", "ide")):
            with self.subTest(agent=agent), self.assertRaises(WeaverError):
                render_provider_setup(spec(agent, surfaces=(surface,)), surface,
                                      provider_id="openrouter", model_id="vendor/model")

    def test_codex_shared_config_route_has_native_fragments_on_local_surfaces(self) -> None:
        for surface in ("cli", "desktop", "ide"):
            with self.subTest(surface=surface):
                artifacts = render_provider_setup(spec("codex", surfaces=(surface,)), surface,
                    provider_id="openrouter", model_id="vendor/model")
                self.assertTrue(any(item.kind == "provider-config" for item in artifacts))
                config = next(item for item in artifacts if item.kind == "provider-config")
                provider = tomllib.loads(config.content.decode())["model_providers"]["openrouter"]
                self.assertEqual(provider["wire_api"], "responses")
                self.assertIs(provider["supports_websockets"], False)
                text = setup_text(artifacts)
                self.assertIn("user-level", text)
                self.assertIn("project-local provider definitions are ignored", text)
                self.assertIn("https://openrouter.ai/api/v1/responses", text)

    def test_chatgpt_openrouter_handoff_is_manual_and_local_work_scoped(self) -> None:
        for surface in ("desktop", "work-local"):
            with self.subTest(surface=surface):
                artifacts = render_provider_setup(spec("chatgpt", surfaces=(surface,)), surface,
                    provider_id="openrouter", model_id="vendor/model")
                self.assertEqual(len(artifacts), 1)
                self.assertEqual(artifacts[0].kind, "provider-setup")
                text = setup_text(artifacts)
                self.assertIn('wire_api = "responses"', text)
                self.assertIn('supports_websockets = false', text)
                self.assertIn("not a Chat conversation, web or Work Cloud provider route", text)
                self.assertIn("user-level", text)
                self.assertIn("matching Codex model metadata", text)
                evidence = provider_evidence("chatgpt", surface)
                self.assertEqual(evidence["state"], "manual")
                self.assertEqual(evidence["checked_on"], "2026-10-26")
        for surface in ("web", "work-cloud"):
            with self.subTest(surface=surface), self.assertRaises(WeaverError):
                provider_evidence("chatgpt", surface)

    def test_byok_wizard_plugin_and_remote_features_do_not_inherit_openrouter_route(self) -> None:
        for product, surface in (("qoder", "cli"), ("lm-studio", "desktop"), ("lm-studio", "bionic"),
                                 ("devin", "desktop-local"), ("kiro", "ide"), ("antigravity", "ide"),
                                 ("perplexity", "app")):
            with self.subTest(product=product, surface=surface), self.assertRaises(WeaverError):
                provider_evidence(product, surface)


if __name__ == "__main__":
    unittest.main()
