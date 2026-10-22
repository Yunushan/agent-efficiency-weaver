import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentweaver.platforms import describe_workflow, detect_host_os

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("windows", "macos", "linux", "bsd", "solaris", "android", "ios")


def surface(workflows=None, evidence=None, **changes):
    values = {
        "id": "cli",
        "mcp_dialect": "json",
        "config_scope": "project",
        "native_path_hint": ".agent/config.json",
        "instruction_mode": "native",
        "skills_path": ".agent/skills",
        "os_workflows": workflows or {},
        "evidence": evidence or {},
    }
    values.update(changes)
    return SimpleNamespace(**values)


def agent(surfaces=None, default_surface="cli", kind="agent", **changes):
    if surfaces is None:
        surfaces = [surface()]
    elif not isinstance(surfaces, (list, tuple, dict)):
        surfaces = [surfaces]
    values = {
        "id": "example",
        "name": "Example Agent",
        "kind": kind,
        "default_surface": default_surface,
        "surfaces": surfaces,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def workflows(routes=None):
    result = {}
    for target in TARGETS:
        entry = {
            "adapter": {"status": "available"},
            "manual": {"status": "available", "description": "Review and merge the staged artifact."},
            "remote": {"status": "unsupported"},
        }
        if routes:
            entry.update(routes.get(target, {}))
        result[target] = entry
    return result


class HostDetectionTests(unittest.TestCase):
    def test_actual_host_is_known(self):
        self.assertIn(detect_host_os(), (*TARGETS, "linux-wsl", "solaris-illumos"))

    def test_detects_supported_hosts(self):
        cases = (
            ({"system": "Windows"}, "windows"),
            ({"system": "Darwin"}, "macos"),
            ({"system": "Linux"}, "linux"),
            ({"system": "Linux", "release": "6.6.0", "wsl": True}, "linux-wsl"),
            ({"system": "Linux", "release": "5.15.90.1-microsoft-standard-WSL2"}, "linux-wsl"),
            ({"system": "FreeBSD", "release": "14.0-RELEASE"}, "bsd-freebsd"),
            ({"system": "OpenBSD", "release": "7.5"}, "bsd-openbsd"),
            ({"system": "NetBSD", "release": "10.0"}, "bsd-netbsd"),
            ({"system": "DragonFly", "release": "6.4"}, "bsd-dragonfly"),
            ({"system": "SunOS", "release": "5.11", "version": "illumos-abc"}, "solaris-illumos"),
            ({"system": "SunOS", "release": "5.11"}, "solaris"),
            ({"system": "Linux", "android": True}, "android"),
            ({"system": "Linux", "sys_platform": "android"}, "android"),
            ({"system": "Linux", "termux": True}, "android"),
            ({"system": "Darwin", "ios": True}, "ios"),
            ({"system": "Darwin", "sys_platform": "ios"}, "ios"),
            ({"system": "Plan9"}, "unknown"),
            ({}, "unknown"),
        )
        for facts, expected in cases:
            with self.subTest(facts=facts):
                self.assertEqual(detect_host_os(facts), expected)
                if facts:
                    self.assertEqual(detect_host_os(**facts), expected)

    def test_plain_linux_and_darwin_are_not_mobile(self):
        self.assertEqual(detect_host_os({"system": "Linux", "release": "6.1"}), "linux")
        self.assertEqual(detect_host_os({"system": "Darwin", "release": "23.0"}), "macos")
        self.assertEqual(detect_host_os({"system": "Windows", "release": "Microsoft"}), "windows")


class WorkflowDescriptionTests(unittest.TestCase):
    def test_partial_target_workflow_does_not_fall_back_to_host_route(self):
        only_linux = {'linux': {'manual': {'status': 'available', 'description': 'Linux route only'}}}
        result = describe_workflow(agent(surface(only_linux)), 'android', 'linux')
        self.assertEqual(result['manual_route']['status'], 'unknown')
        self.assertNotEqual(result['manual_route']['description'], 'Linux route only')

    def test_conditional_core_routes_are_not_unqualified_on_device_claims(self):
        for target in ('bsd', 'solaris', 'android'):
            with self.subTest(target=target):
                result = describe_workflow(agent(surface(workflows())), target, 'windows')
                self.assertFalse(result['workflow']['on_device_cli'])
                self.assertEqual(result['workflow']['core_cli_eligibility'], 'conditional')

    def test_schema_reports_independent_capabilities(self):
        spec = agent(surface(workflows()))
        result = describe_workflow(spec, "windows", "windows")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["agent"]["id"], "example")
        self.assertEqual(result["agent"]["surface"], "cli")
        self.assertEqual(result["target"]["os"], "windows")
        self.assertEqual(result["host"]["os"], "windows")
        self.assertTrue(result["host"]["matches_target"])
        self.assertEqual(result["core_runtime"]["status"], "eligible")
        self.assertTrue(result["core_runtime"]["eligible"])
        self.assertEqual(result["native_vendor"]["status"], "unknown")
        self.assertIsNone(result["native_vendor"]["available"])
        self.assertEqual(result["adapter"]["status"], "available")
        self.assertFalse(result["adapter"]["installed"])
        self.assertEqual(result["manual_route"]["status"], "available")
        self.assertEqual(result["remote_route"]["status"], "unsupported")
        self.assertTrue(result["pending_evidence"])
        self.assertFalse(result["strict_ready"])
        json.dumps(result)

    def test_runtime_evidence_is_required_for_strict_readiness(self):
        documented = {
            "native": {"status": "available", "sources": ["https://example.invalid/docs"]},
        }
        spec = agent(surface(workflows(), documented))
        result = describe_workflow(spec, "windows", "windows")
        self.assertEqual(result["native_vendor"]["status"], "unknown")
        self.assertFalse(result["strict_ready"])

        checked = {
            "native": {
                "status": "available",
                "runtime_checks": [
                    {"os": "windows", "status": "passed", "product_version": "1.2.3", "surface": "cli"}
                ],
            }
        }
        spec = agent(surface(workflows(), checked))
        result = describe_workflow(spec, "windows", "windows")
        self.assertEqual(result["native_vendor"]["status"], "available")
        self.assertTrue(result["native_vendor"]["available"])
        self.assertEqual(result["native_vendor"]["evidence"][0]["product_version"], "1.2.3")
        self.assertTrue(result["strict_ready"])
        self.assertFalse(result["pending_evidence"])

    def test_cross_os_evidence_does_not_authorize_current_host(self):
        checked = {
            "native": {
                "status": "available",
                "runtime_checks": [{"os": "linux", "status": "passed", "surface": "cli"}],
            }
        }
        result = describe_workflow(agent(surface(workflows(), checked)), "linux", "windows")
        self.assertFalse(result["host"]["matches_target"])
        self.assertEqual(result["native_vendor"]["status"], "available")
        self.assertFalse(result["strict_ready"])
        self.assertTrue(any("host" in item["capability"] for item in result["pending_evidence"]))

    def test_wsl_is_not_native_windows_or_plain_linux(self):
        result = describe_workflow(agent(surface(workflows())), "windows", "linux-wsl")
        self.assertEqual(result["host"]["family"], "linux")
        self.assertEqual(result["host"]["qualifier"], "wsl")
        self.assertFalse(result["host"]["matches_target"])
        self.assertFalse(result["strict_ready"])
        self.assertTrue(any("WSL" in warning for warning in result["warnings"]))

        checked = {
            "native": {
                "status": "available",
                "runtime_checks": [{"os": "linux", "status": "passed"}],
            }
        }
        result = describe_workflow(agent(surface(workflows(), checked)), "linux", "linux-wsl")
        self.assertTrue(result["host"]["matches_target"])
        self.assertFalse(result["host"]["qualifier_match"])
        self.assertFalse(result["strict_ready"])

    def test_bsd_variant_is_preserved(self):
        checked = {
            "native": {
                "status": "available",
                "runtime_checks": [{"os": "openbsd", "status": "passed"}],
            }
        }
        result = describe_workflow(agent(surface(workflows(), checked)), "bsd", "bsd-openbsd")
        self.assertEqual(result["host"]["qualifier"], "openbsd")
        self.assertEqual(result["native_vendor"]["status"], "available")
        self.assertEqual(result["native_vendor"]["evidence"][0]["os"], "bsd-openbsd")
        self.assertTrue(result["strict_ready"])

        other = {
            "native": {
                "status": "available",
                "runtime_checks": [{"os": "freebsd", "status": "passed"}],
            }
        }
        result = describe_workflow(agent(surface(workflows(), other)), "bsd", "bsd-openbsd")
        self.assertEqual(result["native_vendor"]["status"], "unknown")
        self.assertFalse(result["strict_ready"])

    def test_solaris_illumos_qualifier_is_preserved(self):
        checked = {
            "native": {
                "status": "available",
                "runtime_checks": [{"os": "illumos", "status": "passed"}],
            }
        }
        result = describe_workflow(agent(surface(workflows(), checked)), "solaris", "solaris-illumos")
        self.assertEqual(result["host"]["qualifier"], "illumos")
        self.assertEqual(result["native_vendor"]["status"], "available")
        self.assertTrue(result["strict_ready"])

    def test_android_and_ios_workflows_are_not_native_cli_claims(self):
        android = workflows({"android": {"mode": "termux", "remote": {"status": "available", "description": "Remote control."}}})
        result = describe_workflow(agent(surface(android)), "android", "android")
        self.assertEqual(result["core_runtime"]["status"], "conditional")
        self.assertEqual(result["workflow"]["mode"], "termux")
        self.assertEqual(result["remote_route"]["status"], "available")
        self.assertFalse(result["strict_ready"])

        ios = workflows({"ios": {"mode": "remote", "remote": {"status": "available", "description": "Remote control."}}})
        result = describe_workflow(agent(surface(ios)), "ios", "ios")
        self.assertEqual(result["core_runtime"]["status"], "unsupported")
        self.assertFalse(result["core_runtime"]["eligible"])
        self.assertEqual(result["workflow"]["mode"], "remote")
        self.assertEqual(result["remote_route"]["status"], "available")
        self.assertFalse(result["strict_ready"])

    def test_registry_shaped_workflows_do_not_claim_runtime(self):
        states = {target: "available" for target in TARGETS}
        states.update({"bsd": "unknown", "solaris": "unknown", "android": "manual", "ios": "unsupported"})
        capability = SimpleNamespace(
            state="available",
            sources=("https://example.invalid/docs",),
            checked_on="2026-10-22",
            version_constraint=None,
            fixture_tests=(),
            runtime_checks=(),
        )
        spec = agent(surface(states, capabilities={"mcp": capability}))
        result = describe_workflow(spec, "windows", "windows")
        self.assertEqual(result["adapter"]["status"], "available")
        self.assertEqual(result["workflow"]["mode"], "documented")
        self.assertEqual(result["native_vendor"]["status"], "unknown")
        self.assertEqual(result["native_vendor"]["documented_state"], "available")
        self.assertFalse(result["strict_ready"])
        self.assertTrue(result["pending_evidence"])

        result = describe_workflow(spec, "ios", "ios")
        self.assertEqual(result["workflow"]["mode"], "unsupported")
        self.assertEqual(result["native_vendor"]["status"], "unsupported")
        self.assertFalse(result["native_vendor"]["available"])
        self.assertFalse(result["strict_ready"])

        result = describe_workflow(spec, "android", "android")
        self.assertEqual(result["workflow"]["mode"], "manual")
        self.assertEqual(result["manual_route"]["status"], "manual")
        self.assertEqual(result["core_runtime"]["status"], "conditional")

    def test_unknown_target_and_surface_fail_closed(self):
        spec = agent(surface(workflows()))
        with self.assertRaises(ValueError):
            describe_workflow(spec, "plan9", "windows")
        with self.assertRaises(ValueError):
            describe_workflow(spec, "windows", "windows", surface_id="missing")

    def test_missing_workflow_remains_unknown(self):
        result = describe_workflow(agent(surface({})), "windows", "windows")
        self.assertEqual(result["adapter"]["status"], "available")
        self.assertEqual(result["manual_route"]["status"], "unknown")
        self.assertEqual(result["remote_route"]["status"], "unknown")
        self.assertEqual(result["workflow"]["mode"], "unknown")
        self.assertFalse(result["strict_ready"])

    def test_mapping_surfaces_and_explicit_surface_selection(self):
        surfaces = {
            "cli": surface(workflows()),
            "ide": surface(workflows(), id="ide", native_path_hint=".agent/ide.json"),
        }
        result = describe_workflow(agent(surfaces, default_surface="ide"), "linux", "linux")
        self.assertEqual(result["agent"]["surface"], "ide")
        self.assertEqual(result["adapter"]["config_path_hint"], ".agent/ide.json")

    def test_unknown_host_is_explicit(self):
        result = describe_workflow(agent(surface(workflows())), "macos", "unknown")
        self.assertEqual(result["host"]["os"], "unknown")
        self.assertFalse(result["host"]["matches_target"])
        self.assertFalse(result["strict_ready"])
        self.assertTrue(result["warnings"])


class ScriptTests(unittest.TestCase):
    def test_installers_are_path_relative_and_unprivileged(self):
        ps1 = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        sh = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        self.assertIn("PSScriptRoot", ps1)
        self.assertIn("--user", ps1)
        self.assertNotIn("sudo", ps1.lower())
        self.assertIn("CDPATH=", sh)
        self.assertIn("--user", sh)
        self.assertNotIn("sudo", sh.lower())
        self.assertNotIn("bash", sh.splitlines()[0].lower())

    def test_verification_script_help_has_no_side_effects(self):
        script = ROOT / "scripts/verify-platform.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--output", completed.stdout)


if __name__ == "__main__":
    unittest.main()
