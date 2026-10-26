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


if __name__ == "__main__":
    unittest.main()
