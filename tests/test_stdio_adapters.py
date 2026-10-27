"""Synthetic stdio launch fixtures; no package is installed or executed."""
import copy
import json
import os
import socket
import subprocess
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import yaml

from agentweaver.adapters import render_mcp, stdio_inventory
from agentweaver.artifacts import write_artifacts
from agentweaver.config import WeaverError, load_json, render, select_servers, validate_catalog
from agentweaver.integrations import load_integrations
from tests.test_cli_exports import invoke

ROOT = Path(__file__).resolve().parents[1]
COMMAND = "synthetic-mcp-fixture"
ARGS = ["--fixture-only", "not-an-installed-package"]
STDIO = dict(kind="stdio", command=COMMAND, args=ARGS, default_enabled=False,
             description="Synthetic argv fixture; never installed or executed.")
PLAIN = {"abacus", "amp", "antigravity", "claude-code", "cline", "devin", "junie", "kiro",
         "pi", "qoder", "zed", "kimi-code", "qwen-code", "openhands"}
TYPE_STDIO = {"copilot", "cursor", "factory", "lm-studio"}
LOCAL_ARRAY = {"kilo", "opencode"}
SUPPORTED = PLAIN | TYPE_STDIO | LOCAL_ARRAY | {"codex", "goose", "hermes"}


class StdioConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        self.catalog["servers"]["synthetic-stdio"] = copy.deepcopy(STDIO)
        self.profile = load_json(ROOT / "profiles/efficient.json")
        self.profile.update(allowed_mcp=["context7", "synthetic-stdio"], max_mcp_servers=2)
        self.integrations = load_integrations(ROOT)

    def artifact(self, product, surface=None, enabled=None):
        spec = self.integrations[product]
        return render_mcp(spec, surface or spec.default_surface, self.profile,
                          self.catalog, enabled if enabled is not None else ["synthetic-stdio"])[0]

    def test_stdio_catalog_is_strict_default_off_and_selection_keeps_remote_contract(self):
        self.assertEqual(validate_catalog(self.catalog), [])
        self.assertEqual(select_servers(self.profile, self.catalog, []), {})
        selected = select_servers(self.profile, self.catalog, ["synthetic-stdio", "context7"])
        self.assertEqual(selected["context7"], "https://mcp.context7.com/mcp")
        self.assertEqual(selected["synthetic-stdio"], dict(kind="stdio", command=COMMAND, args=ARGS))
        self.assertEqual(list(selected), ["context7", "synthetic-stdio"])
        selected["synthetic-stdio"]["args"].append("changed-output")
        self.assertEqual(self.catalog["servers"]["synthetic-stdio"]["args"], ARGS)

    def test_stdio_validation_rejects_shell_strings_expansion_controls_and_unbounded_argv(self):
        invalid_changes = [
            {"command": "npx -y package"}, {"command": "npx;execute"}, {"command": "npx|execute"},
            {"command": "python scripts/server.py"}, {"command": "node ./server.js"},
            {"command": "$(helper)"}, {"command": "${EXECUTABLE}"}, {"command": ""},
            {"command": ["npx", "-y"]}, {"command": "bad\ud800"}, {"command": "bad\u202e"},
            {"command": "x" * 1025}, {"args": "--raw shell string"}, {"args": None},
            {"args": [None]}, {"args": [[]]}, {"args": ["bad\nline"]}, {"args": ["bad\ud800"]},
            {"args": ["${SECRET}"]}, {"args": ["$SECRET"]}, {"args": ["%SECRET%"]},
            {"args": ["{env:SECRET}"]}, {"args": ["x" * 4097]}, {"args": ["x"] * 129},
            {"args": ["x" * 4096] * 9}, {"default_enabled": True}, {"default_enabled": 0},
            {"url": "https://example.invalid/mcp"}, {"env": {}}, {"headers": {}},
            {"cwd": "."}, {"shell": True}, {"permissions": {"allow": True}},
        ]
        for changes in invalid_changes:
            catalog = copy.deepcopy(self.catalog)
            catalog["servers"]["synthetic-stdio"].update(changes)
            with self.subTest(changes=repr(changes)):
                self.assertTrue(validate_catalog(catalog))
                with self.assertRaises(WeaverError):
                    select_servers(self.profile, catalog, [])
        for missing in ("command", "args", "default_enabled"):
            catalog = copy.deepcopy(self.catalog)
            del catalog["servers"]["synthetic-stdio"][missing]
            with self.subTest(missing=missing):
                self.assertTrue(validate_catalog(catalog))

    def test_secret_like_argv_and_unknown_fields_are_redacted_in_errors(self):
        secret = "NEVER-ECHO"
        for value in (
            f"--api-key={secret}", "--token", f"TOKEN={secret}", f"password={secret}",
            f"Bearer {secret}", f"Basic {secret}", f"https://user:{secret}@example.invalid",
            "sk-" + "x" * 24, "ghp_" + "x" * 24,
        ):
            catalog = copy.deepcopy(self.catalog)
            catalog["servers"]["synthetic-stdio"]["args"] = [value]
            with self.subTest(value=value), self.assertRaises(WeaverError) as raised:
                select_servers(self.profile, catalog, ["synthetic-stdio"])
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(value, str(raised.exception))
        for field in ("env", "api_key", "headers", "authorization", secret):
            catalog = copy.deepcopy(self.catalog)
            catalog["servers"]["synthetic-stdio"][field] = secret
            with self.subTest(field=field), self.assertRaises(WeaverError) as raised:
                select_servers(self.profile, catalog, [])
            self.assertNotIn(secret, str(raised.exception))

    def test_stdio_requires_explicit_selection_allowlist_and_profile_capacity(self):
        for product in SUPPORTED:
            with self.subTest(product=product):
                self.assertNotIn(COMMAND.encode(), self.artifact(product, enabled=[]).content)
        unpermitted = dict(self.profile, allowed_mcp=["context7"])
        with self.assertRaises(WeaverError):
            select_servers(unpermitted, self.catalog, ["synthetic-stdio"])
        zero = load_json(ROOT / "profiles/enterprise-secure.json")
        self.assertEqual(select_servers(zero, self.catalog, []), {})
        with self.assertRaises(WeaverError):
            select_servers(zero, self.catalog, ["synthetic-stdio"])

    def test_each_supported_host_uses_its_documented_native_shape(self):
        metadata = stdio_inventory()
        self.assertEqual(set(metadata), SUPPORTED)
        for product in SUPPORTED:
            spec = self.integrations[product]
            for surface in spec.surfaces:
                if surface.mcp_dialect != metadata[product]["mcp_dialect"]:
                    continue
                with self.subTest(product=product, surface=surface.id):
                    artifact = self.artifact(product, surface.id)
                    text = artifact.content.decode()
                    parsed = tomllib.loads(text) if product == "codex" else json.loads(text)
                    root = ("mcp_servers" if product in {"codex", "hermes"} else
                            "mcp" if product in LOCAL_ARRAY else "servers" if product == "copilot" else
                            "context_servers" if product == "zed" else "extensions" if product == "goose" else
                            "amp.mcpServers" if product == "amp" else "mcpServers")
                    entry = parsed[root]["synthetic-stdio"]
                    base = dict(command=COMMAND, args=ARGS)
                    expected = ({"type": "stdio", **base} if product in TYPE_STDIO else
                                {"type": "local", "command": [COMMAND, *ARGS], "enabled": True} if product in LOCAL_ARRAY else
                                {"type": "stdio", "name": "synthetic-stdio", "enabled": True, "cmd": COMMAND, "args": ARGS} if product == "goose" else
                                {**base, "enabled": True} if product == "hermes" else base)
                    self.assertEqual(entry, expected)
                    for remote_field in ("url", "serverUrl", "httpUrl", "uri"):
                        self.assertNotIn(remote_field, entry)
                    self.assertNotIn("env", entry)
                    self.assertFalse(metadata[product]["runtime_checks"])
                    self.assertEqual(metadata[product]["checked_on"], "2026-10-26")
                    self.assertTrue(metadata[product]["sources"])

    def test_mixed_remote_and_stdio_keep_existing_http_shapes(self):
        golden = load_json(ROOT / "tests/fixtures/adapters/golden.json")
        for product in SUPPORTED:
            with self.subTest(product=product):
                artifact = self.artifact(product, enabled=["context7", "synthetic-stdio"])
                parsed = tomllib.loads(artifact.content.decode()) if product == "codex" else json.loads(artifact.content)
                expected = golden[product]["enabled"]
                expected = tomllib.loads(expected) if product == "codex" else expected
                root = next(iter(expected))
                self.assertEqual(parsed[root]["context7"], expected[root]["context7"])

    def test_user_scope_fragments_and_yaml_compatibility_are_preserved(self):
        for product in ("cline", "zed", "goose", "hermes", "lm-studio", "openhands"):
            artifact = self.artifact(product)
            with self.subTest(product=product):
                self.assertIn("fragments", artifact.relative_path.parts)
                self.assertEqual(artifact.kind, "mcp-fragment")
                if product in {"goose", "hermes"}:
                    self.assertEqual(yaml.safe_load(artifact.content), json.loads(artifact.content))

    def test_remote_only_request_and_unestablished_native_stdio_fail_closed(self):
        for product, surface in (("perplexity", "agent-api"), ("lm-studio", "api"),
                                 ("jetbrains-air", "desktop")):
            with self.subTest(product=product), self.assertRaises(WeaverError) as raised:
                self.artifact(product, surface)
            self.assertIn("stdio", str(raised.exception))
        spec = self.integrations["abacus"]
        changed = replace(spec, surfaces=(replace(spec.surface("desktop"), mcp_dialect="context_servers.url"),))
        with self.assertRaises(WeaverError):
            render_mcp(changed, "desktop", self.profile, self.catalog, ["synthetic-stdio"])

    def test_legacy_kilo_and_abacus_routes_keep_stdio_structured(self):
        kilo = render("kilo", self.profile, self.catalog, ["synthetic-stdio"])
        abacus = render("abacus", self.profile, self.catalog, ["synthetic-stdio"])
        self.assertEqual(kilo["mcp"]["synthetic-stdio"],
                         {"type": "local", "command": [COMMAND, *ARGS], "enabled": True})
        self.assertEqual(abacus["mcpServers"]["synthetic-stdio"], {"command": COMMAND, "args": ARGS})

    def test_unicode_quoted_arguments_and_executable_paths_round_trip_without_expansion(self):
        command = "C:\\Program Files\\synthetic-fixture\\server.exe"
        args = ["資料", 'quoted "argument"', "", "semi;colon", "a&b"]
        self.catalog["servers"]["synthetic-stdio"].update(command=command, args=args)
        for product in ("abacus", "codex", "kilo", "goose", "openhands"):
            artifact = self.artifact(product)
            parsed = tomllib.loads(artifact.content.decode()) if product == "codex" else json.loads(artifact.content)
            root = "mcp_servers" if product == "codex" else "mcp" if product == "kilo" else "extensions" if product == "goose" else "mcpServers"
            entry = parsed[root]["synthetic-stdio"]
            with self.subTest(product=product):
                self.assertEqual(entry["cmd" if product == "goose" else "command"], [command, *args] if product == "kilo" else command)
                if product != "kilo":
                    self.assertEqual(entry["args"], args)

    def test_openhands_manual_json_stdio_fragment_and_cli_dry_run_keep_native_path_safe(self):
        command = "C:\\Program Files\\synthetic-fixture\\server.exe"
        args = ["--fixture-only", "資料", 'quoted "argument"', "", "semi;colon", "a&b"]
        self.catalog["servers"]["synthetic-stdio"].update(command=command, args=args)
        empty = self.artifact("openhands", enabled=[])
        self.assertEqual(json.loads(empty.content), {"mcpServers": {}})
        artifact = self.artifact("openhands")
        self.assertEqual(artifact.relative_path.as_posix(), "openhands/fragments/mcp.json")
        self.assertEqual(artifact.kind, "mcp-fragment")
        self.assertEqual(json.loads(artifact.content), {
            "mcpServers": {"synthetic-stdio": {"command": command, "args": args}}})
        metadata = stdio_inventory()["openhands"]
        self.assertEqual(metadata["schema"], "command-args")
        self.assertTrue(any("mcp-servers.mdx" in source for source in metadata["sources"]))
        self.assertTrue(any("283c6ac3610f4299f405f7389f65766dcb83e12a" in source for source in metadata["sources"]))
        self.assertEqual(metadata["runtime_checks"], [])
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            fixture_root = Path(directory)
            (fixture_root / "catalogs").mkdir()
            (fixture_root / "profiles").mkdir()
            for filename in ("integrations.json", "operating-systems.json", "requested-coverage.json"):
                (fixture_root / "catalogs" / filename).write_bytes((ROOT / "catalogs" / filename).read_bytes())
            (fixture_root / "catalogs/mcp-servers.json").write_text(json.dumps(self.catalog), encoding="utf-8")
            (fixture_root / "profiles/efficient.json").write_text(json.dumps(self.profile), encoding="utf-8")
            output = fixture_root / "not-created-by-dry-run"
            with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                    mock.patch.object(subprocess, "run", side_effect=AssertionError("process used")), \
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")), \
                    mock.patch.object(Path, "home", side_effect=AssertionError("home read")), \
                    mock.patch("agentweaver.platforms.collect_host_facts", return_value={
                        "system": "Windows", "sys_platform": "win32", "release": "11", "version": "fixture-build"}), \
                    mock.patch("agentweaver.platforms.platform.machine", return_value="AMD64"):
                code, text, error = invoke("render", "--root", str(fixture_root), "--agent", "openhands",
                    "--profile", "efficient", "--enable", "synthetic-stdio", "--output-dir", str(output), "--dry-run")
            self.assertEqual(code, 0, error)
            self.assertFalse(output.exists())
            staged = next(item for item in json.loads(text)["artifacts"] if item["kind"] == "mcp-fragment")
            self.assertEqual(staged["path"], artifact.relative_path.as_posix())
            self.assertEqual(json.loads(staged["content"]), json.loads(artifact.content))
            write_artifacts(output, [artifact])
            target = output / artifact.relative_path
            target.write_text("user edited configuration\n", encoding="utf-8")
            with self.assertRaises(WeaverError):
                write_artifacts(output, [artifact], force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "user edited configuration\n")

    def test_staging_never_executes_resolves_environment_or_overwrites_modified_artifacts(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("process used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")), \
                mock.patch.object(os, "getenv", side_effect=AssertionError("environment read")), \
                mock.patch.object(Path, "home", side_effect=AssertionError("home read")):
            artifact = self.artifact("codex")
            self.assertEqual(artifact, self.artifact("codex"))
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            destination = Path(directory).resolve()
            target = destination / artifact.relative_path
            self.assertFalse(target.exists())
            write_artifacts(destination, [artifact])
            with self.assertRaises(WeaverError):
                write_artifacts(destination, [artifact])
            target.write_text("user change\n", encoding="utf-8")
            with self.assertRaises(WeaverError):
                write_artifacts(destination, [artifact], force=True)
            self.assertEqual(target.read_text(), "user change\n")

    def test_perplexity_catalog_entry_is_real_remote_and_default_off_without_profile_enabling(self):
        catalog = load_json(ROOT / "catalogs/mcp-servers.json")
        entry = catalog["servers"]["perplexity"]
        self.assertEqual(entry["kind"], "remote")
        self.assertEqual(entry["url"], "https://api.perplexity.ai/mcp")
        self.assertIs(entry["default_enabled"], False)
        self.assertIn("Authenticate manually", entry["description"])
        self.assertFalse(any(server["kind"] == "stdio" for server in catalog["servers"].values()))
        for path in (ROOT / "profiles").glob("*.json"):
            self.assertNotIn("perplexity", load_json(path)["allowed_mcp"])


if __name__ == "__main__":
    unittest.main()
