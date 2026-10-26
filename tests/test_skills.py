import copy
import hashlib
import shutil
import socket
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agentweaver.artifacts import write_artifacts
from agentweaver.config import WeaverError, load_json
from agentweaver.integrations import CapabilityEvidence, get_integration, load_integrations
from agentweaver.skills import render_instruction_bundle, validate_skills

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"
EXPECTED_SKILLS = {
    "benchmark-design",
    "gitlab-ci-review",
    "linux-incident-triage",
    "rke2-troubleshooting",
    "security-review",
    "token-efficient-retrieval",
}

NATIVE_TARGETS = {
    ("abacus", "desktop"): "AGENTS.md",
    ("aider", "cli"): "CONVENTIONS.md",
    ("amp", "cli"): "AGENTS.md",
    ("antigravity", "ide"): "AGENTS.md",
    ("claude-code", "cli"): "CLAUDE.md",
    ("cline", "ide"): "AGENTS.md",
    ("codex", "cli"): "AGENTS.md",
    ("copilot", "vscode"): ".github/copilot-instructions.md",
    ("cursor", "ide"): "AGENTS.md",
    ("devin", "desktop-local"): "AGENTS.md",
    ("goose", "cli"): ".goosehints",
    ("junie", "cli"): ".junie/AGENTS.md",
    ("kilo", "cli"): "AGENTS.md",
    ("kilo", "ide"): "AGENTS.md",
    ("opencode", "cli"): "AGENTS.md",
    ("openhands", "cli"): "AGENTS.md",
    ("pi", "cli"): "AGENTS.md",
    ("zed", "ide"): ".rules",
}

MANUAL_TARGETS = {
    ("kiro", "ide"),
    ("qoder", "cli"),
}


def skill_path(root, name):
    return root / ".agents" / "skills" / name / "SKILL.md"


def read_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    end = lines.index("---", 1)
    values = {}
    for line in lines[1:end]:
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def copy_canonical(destination):
    shutil.copytree(SKILLS_ROOT, destination / ".agents" / "skills")


def write_skill(root, name, *, description="A valid test skill.", body="Body.\n", extra=""):
    path = skill_path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        f"name: {name}\n"
        f'description: "{description}"\n'
        f"{extra}"
        "---\n\n"
        f"# {name}\n\n"
        f"{body}"
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_six_skills(root, body_size=0):
    for name in sorted(EXPECTED_SKILLS):
        write_skill(root, name, body="x" * body_size + "\n")


class SkillValidationTests(unittest.TestCase):
    def test_canonical_skills_validate_and_are_exactly_six(self):
        self.assertEqual(validate_skills(ROOT), [])
        folders = sorted(path.name for path in SKILLS_ROOT.iterdir())
        self.assertEqual(folders, sorted(EXPECTED_SKILLS))
        for name in EXPECTED_SKILLS:
            metadata = read_frontmatter(skill_path(ROOT, name))
            self.assertEqual(metadata["name"], name)
            self.assertTrue(metadata["description"])

    def test_missing_skills_directory_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertTrue(validate_skills(Path(temporary)))

    def test_malformed_frontmatter_and_skill_files_are_rejected(self):
        cases = {
            "missing frontmatter": (
                "security-review",
                "# missing\n",
                "front matter",
            ),
            "missing description": (
                "security-review",
                "---\nname: security-review\n---\n\nbody\n",
                "description",
            ),
            "missing name": (
                "security-review",
                '---\ndescription: "x"\n---\n\nbody\n',
                "name",
            ),
            "malformed field": (
                "security-review",
                '---\nname: security-review\ndescription "x"\n---\n',
                "malformed",
            ),
            "duplicate field": (
                "security-review",
                '---\nname: security-review\nname: security-review\ndescription: "x"\n---\n',
                "duplicate",
            ),
            "unknown field": (
                "security-review",
                '---\nname: security-review\ndescription: "x"\nlicense: nope\n---\n',
                "unknown",
            ),
            "folder mismatch": (
                "security-review",
                '---\nname: benchmark-design\ndescription: "x"\n---\n',
                "does not match",
            ),
            "unsafe name": (
                "security review",
                '---\nname: security-review\ndescription: "x"\n---\n',
                "invalid",
            ),
            "crlf": (
                "security-review",
                '---\rname: security-review\rdescription: "x"\r---\r',
                "line endings",
            ),
            "control character": (
                "security-review",
                '---\nname: security-review\ndescription: "x\x01"\n---\n',
                "control",
            ),
        }
        for label, (folder, content, expected) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                copy_canonical(root)
                path = skill_path(root, folder)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content.encode("utf-8"))
                errors = validate_skills(root)
                self.assertTrue(any(expected.casefold() in error.casefold() for error in errors), errors)

    def test_crlf_checkout_is_accepted_and_normalized_for_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_canonical(root)
            path = skill_path(root, "security-review")
            path.write_bytes(
                path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            )
            self.assertEqual(validate_skills(root), [])
            spec = get_integration("codex", ROOT)
            profile = load_json(ROOT / "profiles/efficient.json")
            artifact = render_instruction_bundle(
                spec, "cli", profile, ["security-review"], root
            )[1]
            self.assertNotIn(b"\r", artifact.content)
            self.assertEqual(
                artifact.content,
                skill_path(ROOT, "security-review").read_bytes().replace(b"\r\n", b"\n"),
            )

    def test_missing_skill_file_extra_entries_and_missing_six_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_canonical(root)
            skill_path(root, "security-review").unlink()
            self.assertTrue(validate_skills(root))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_canonical(root)
            (root / ".agents" / "skills" / "stray.txt").write_text("x", encoding="utf-8")
            self.assertTrue(validate_skills(root))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_six_skills(root)
            shutil.rmtree(root / ".agents" / "skills" / "security-review")
            errors = validate_skills(root)
            self.assertTrue(any("six" in error.casefold() for error in errors), errors)

    def test_duplicate_names_and_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_canonical(root)
            source = skill_path(root, "security-review")
            duplicate = root / ".agents" / "skills" / "Security-Review" / "SKILL.md"
            try:
                duplicate.parent.mkdir()
            except FileExistsError:
                self.skipTest("filesystem is case-insensitive")
            duplicate.write_text(
                '---\nname: Security-Review\ndescription: "duplicate"\n---\n', encoding="utf-8"
            )
            errors = validate_skills(root)
            self.assertTrue(any("duplicate" in error.casefold() for error in errors), errors)
            self.assertTrue(source.exists())

    def test_skill_file_link_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_canonical(root)
            path = skill_path(root, "security-review")
            target = path.with_name("target.md")
            path.replace(target)
            try:
                path.symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            errors = validate_skills(root)
            self.assertTrue(any("link" in error.casefold() for error in errors), errors)


class InstructionBundleTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_json(ROOT / "profiles/efficient.json")
        self.integrations = load_integrations(ROOT)

    def test_every_agent_surface_has_a_native_manual_gap_or_provider_route(self):
        covered = set()
        gaps = set()
        for agent_id, spec in self.integrations.items():
            if spec.kind == "provider":
                with self.assertRaises(WeaverError):
                    render_instruction_bundle(spec, spec.default_surface, self.profile, [], ROOT)
                continue
            surfaces = spec.surfaces.values() if isinstance(spec.surfaces, dict) else spec.surfaces
            for surface in surfaces:
                key = (agent_id, surface.id)
                covered.add(key)
                with self.subTest(agent=agent_id, surface=surface.id):
                    evidence = surface.capabilities["instructions"]
                    if evidence.state in {"unsupported", "unknown"} or not evidence.sources:
                        gaps.add(key)
                        with self.assertRaises(WeaverError) as raised:
                            render_instruction_bundle(spec, surface.id, self.profile, [], ROOT)
                        message = str(raised.exception).casefold()
                        self.assertIn("instruction", message)
                        self.assertTrue(
                            evidence.state in message or "source evidence" in message,
                            message,
                        )
                        continue
                    artifacts = render_instruction_bundle(spec, surface.id, self.profile, [], ROOT)
                    self.assertTrue(artifacts)
                    if key in NATIVE_TARGETS:
                        expected_path = f"{agent_id}/{NATIVE_TARGETS[key]}"
                        self.assertEqual(artifacts[0].relative_path.as_posix(), expected_path)
                        self.assertEqual(artifacts[0].kind, "instructions")
                        self.assertIn("advisory", artifacts[0].content.decode("utf-8").casefold())
                    else:
                        self.assertIn(key, MANUAL_TARGETS)
                        self.assertEqual(
                            artifacts[0].relative_path.as_posix(),
                            f"{agent_id}/handoff/INSTRUCTIONS.md",
                        )
                        self.assertEqual(artifacts[0].kind, "handoff")
                        text = artifacts[0].content.decode("utf-8")
                        self.assertIn(surface.capabilities["instructions"].sources[0], text)
                        self.assertIn("manual", text.casefold())
        expected = {
            (agent_id, surface.id)
            for agent_id, spec in self.integrations.items()
            if spec.kind == "agent"
            for surface in (spec.surfaces.values() if isinstance(spec.surfaces, dict) else spec.surfaces)
        }
        self.assertEqual(covered, expected)
        self.assertEqual(len({agent for agent, _ in covered}), 19)
        self.assertEqual(gaps & set(NATIVE_TARGETS), set())
        self.assertEqual(gaps & MANUAL_TARGETS, set())

    def test_profile_advice_is_advisory_and_infrastructure_is_retained(self):
        spec = get_integration("codex", ROOT)
        text = render_instruction_bundle(spec, "cli", self.profile, [], ROOT)[0].content.decode("utf-8")
        folded = text.casefold()
        for phrase in (
            "advisory",
            "not tool enforcement",
            "ambient configuration is not inspected",
            "infrastructure prerequisites",
            "production writes",
            "retrieval",
        ):
            self.assertIn(phrase, folded)
        self.assertIn(self.profile["id"], text)
        self.assertIn(self.profile["description"], text)

    def test_selected_skills_are_copied_byte_for_byte_when_native(self):
        selected = ["token-efficient-retrieval", "security-review"]
        spec = get_integration("codex", ROOT)
        artifacts = render_instruction_bundle(spec, "cli", self.profile, selected, ROOT)
        skill_artifacts = [item for item in artifacts if item.kind == "skill"]
        self.assertEqual(len(skill_artifacts), 2)
        for name, artifact in zip(sorted(selected), skill_artifacts):
            expected_path = f"codex/.agents/skills/{name}/SKILL.md"
            self.assertEqual(artifact.relative_path.as_posix(), expected_path)
            expected_bytes = skill_path(ROOT, name).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(artifact.content, expected_bytes)
            self.assertNotIn(b"\r", artifact.content)
        instruction = artifacts[0].content.decode("utf-8")
        self.assertIn(".agents/skills", instruction)
        self.assertNotIn("codex/.agents/skills", instruction)
        self.assertIn("LF-normalized", instruction)
        self.assertNotIn("# security review", instruction)
        self.assertNotIn("# token efficient retrieval", instruction)

    def test_empty_skill_selection_copies_nothing(self):
        for agent_id in ("codex", "abacus", "aider", "kiro", "cline"):
            spec = get_integration(agent_id, ROOT)
            with self.subTest(agent=agent_id):
                artifacts = render_instruction_bundle(
                    spec, spec.default_surface, self.profile, [], ROOT
                )
                self.assertFalse(
                    [item for item in artifacts if item.kind in {"skill", "read-file"}]
                )
                self.assertIn("none selected", artifacts[0].content.decode("utf-8").casefold())

    def test_abacus_uses_only_root_agents_md_and_no_native_skill_directory(self):
        spec = get_integration("abacus", ROOT)
        selected = sorted(EXPECTED_SKILLS)
        artifacts = render_instruction_bundle(spec, "desktop", self.profile, selected, ROOT)
        self.assertEqual(artifacts[0].relative_path.as_posix(), "abacus/AGENTS.md")
        self.assertEqual(artifacts[0].kind, "instructions")
        self.assertEqual(
            [item.relative_path.as_posix() for item in artifacts[1:]],
            [f"abacus/read/{name}.md" for name in selected],
        )
        self.assertFalse([item for item in artifacts if item.kind == "skill"])
        self.assertFalse(any(".abacusai/skills" in item.relative_path.as_posix() for item in artifacts))
        for name, artifact in zip(selected, artifacts[1:]):
            self.assertEqual(artifact.kind, "read-file")
            self.assertEqual(
                artifact.content,
                skill_path(ROOT, name).read_bytes().replace(b"\r\n", b"\n"),
            )
        text = artifacts[0].content.decode("utf-8")
        self.assertNotIn(".agents/skills", text)
        for name in selected:
            self.assertIn(f"read/{name}.md", text)

    def test_aider_gets_conventions_read_files_and_no_native_skill_claim(self):
        spec = get_integration("aider", ROOT)
        artifacts = render_instruction_bundle(
            spec, "cli", self.profile, ["security-review"], ROOT
        )
        self.assertEqual(
            [item.relative_path.as_posix() for item in artifacts],
            ["aider/CONVENTIONS.md", "aider/read/security-review.md"],
        )
        self.assertFalse([item for item in artifacts if item.kind == "skill"])
        self.assertEqual(artifacts[1].kind, "read-file")
        self.assertEqual(
            artifacts[1].content,
            skill_path(ROOT, "security-review").read_bytes().replace(b"\r\n", b"\n"),
        )
        text = artifacts[0].content.decode("utf-8")
        folded = text.casefold()
        self.assertIn("--read", text)
        self.assertIn("CONVENTIONS.md", text)
        self.assertIn("read/security-review.md", text)
        self.assertNotIn(".agents/skills", text)
        self.assertIn("manual", folded)
        self.assertNotIn("native skill", folded)
        self.assertNotIn("skills directory", folded)

    def test_manual_skills_are_staged_as_portable_read_files(self):
        for agent_id in ("aider", "abacus", "cline", "cursor"):
            spec = get_integration(agent_id, ROOT)
            with self.subTest(agent=agent_id):
                artifacts = render_instruction_bundle(
                    spec, spec.default_surface, self.profile, ["benchmark-design"], ROOT
                )
                self.assertFalse([item for item in artifacts if item.kind == "skill"])
                read_files = [item for item in artifacts if item.kind == "read-file"]
                self.assertEqual(len(read_files), 1)
                self.assertEqual(
                    read_files[0].relative_path.as_posix(),
                    f"{agent_id}/read/benchmark-design.md",
                )
                self.assertEqual(
                    read_files[0].content,
                    skill_path(ROOT, "benchmark-design").read_bytes().replace(b"\r\n", b"\n"),
                )
                text = artifacts[0].content.decode("utf-8")
                self.assertIn("benchmark-design", text)
                self.assertIn("read/benchmark-design.md", text)
                self.assertIn("LF-normalized content", text)
                self.assertNotIn("with unchanged content", text)
                self.assertNotIn(".agents/skills/benchmark-design", text)

    def test_manual_read_files_survive_temporary_resource_cleanup(self):
        with tempfile.TemporaryDirectory() as source_directory:
            source = Path(source_directory)
            copy_canonical(source)
            expected = skill_path(source, "security-review").read_bytes().replace(b"\r\n", b"\n")
            spec = get_integration("cline", ROOT)
            artifacts = render_instruction_bundle(
                spec, "ide", self.profile, ["security-review"], source
            )
            with tempfile.TemporaryDirectory() as output_directory:
                output = Path(output_directory).resolve()
                write_artifacts(output, artifacts)
                shutil.rmtree(source)
                self.assertEqual(
                    (output / "cline/read/security-review.md").read_bytes(),
                    expected,
                )
                self.assertIn(
                    "read/security-review.md",
                    (output / "cline/AGENTS.md").read_text(encoding="utf-8"),
                )

    def test_direct_api_enforces_profile_contract_and_enterprise_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_six_skills(root)
            spec = get_integration("codex", ROOT)
            invalid = {
                "top-level key": dict(self.profile, unexpected=True),
                "policy key": dict(
                    self.profile,
                    policy={"allow_production_writes": False, "unexpected": True},
                ),
                "policy write": dict(
                    self.profile, policy={"allow_production_writes": True}
                ),
                "retrieval key": dict(
                    self.profile,
                    retrieval={"max_snippets": 8, "avoid_full_repo_dumps": True, "x": 1},
                ),
                "retrieval missing": dict(
                    self.profile, retrieval={"max_snippets": 8}
                ),
                "retrieval zero": dict(
                    self.profile,
                    retrieval={"max_snippets": 0, "avoid_full_repo_dumps": True},
                ),
                "boolean limit": dict(self.profile, max_mcp_servers=True),
            }
            for label, profile in invalid.items():
                with self.subTest(label=label), self.assertRaises(WeaverError):
                    render_instruction_bundle(spec, "cli", profile, [], root)

            enterprise = load_json(ROOT / "profiles/enterprise-secure.json")
            self.assertTrue(
                render_instruction_bundle(spec, "cli", enterprise, [], root)
            )
            enterprise_max = copy.deepcopy(enterprise)
            enterprise_max["max_mcp_servers"] = 1
            enterprise_allowed = copy.deepcopy(enterprise)
            enterprise_allowed["allowed_mcp"] = ["context7"]
            for label, profile in (
                ("enterprise limit", enterprise_max),
                ("enterprise allowlist", enterprise_allowed),
            ):
                with self.subTest(label=label), self.assertRaises(WeaverError):
                    render_instruction_bundle(spec, "cli", profile, [], root)

    def test_provider_only_surface_without_instruction_evidence_reports_gap(self):
        qoder = get_integration("qoder", ROOT)
        base = qoder.surface("cli")
        unknown = CapabilityEvidence(
            state="unknown",
            sources=(),
            checked_on=None,
            version_constraint=None,
            fixture_tests=(),
            runtime_checks=(),
        )
        capabilities = dict(base.capabilities)
        capabilities["instructions"] = unknown
        capabilities["skills"] = unknown
        capabilities["mcp"] = unknown
        provider_only = replace(
            base,
            id="ide",
            mcp_dialect=None,
            config_scope="none",
            native_path_hint=None,
            instruction_mode="none",
            instruction_paths=(),
            skills_path=None,
            capabilities=capabilities,
        )
        spec = replace(qoder, default_surface="ide", surfaces=(provider_only,))
        with self.assertRaises(WeaverError) as raised:
            render_instruction_bundle(spec, "ide", self.profile, [], ROOT)
        message = str(raised.exception).casefold()
        self.assertIn("unavailable", message)
        self.assertIn("unknown", message)

    def test_codex_and_abacus_instruction_limits_fail_without_truncation(self):
        codex = get_integration("codex", ROOT)
        large = dict(self.profile, description="x" * 40000)
        with self.assertRaises(WeaverError) as raised:
            render_instruction_bundle(codex, "cli", large, [], ROOT)
        self.assertIn("32", str(raised.exception))

        abacus = get_integration("abacus", ROOT)
        with self.assertRaises(WeaverError) as raised:
            render_instruction_bundle(abacus, "desktop", large, [], ROOT)
        self.assertIn("40,000", str(raised.exception))

    def test_copied_skill_bytes_do_not_count_against_instruction_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_six_skills(root, body_size=9000)
            self.assertEqual(validate_skills(root), [])
            codex = get_integration("codex", ROOT)
            artifacts = render_instruction_bundle(
                codex, "cli", self.profile, sorted(EXPECTED_SKILLS), root
            )
            self.assertEqual(len([item for item in artifacts if item.kind == "skill"]), 6)
            self.assertLess(len(artifacts[0].content), 32 * 1024)
            total_skill_bytes = sum(
                len(item.content) for item in artifacts if item.kind == "skill"
            )
            self.assertGreater(total_skill_bytes, 32 * 1024)

    def test_skill_selection_rejects_traversal_duplicates_unknown_and_non_lists(self):
        spec = get_integration("codex", ROOT)
        selections = (
            ["../security-review"],
            ["security-review/../../security-review"],
            ["/security-review"],
            ["security-review/SKILL.md"],
            [r"security-review\SKILL.md"],
            [""],
            ["security review"],
            ["not-a-skill"],
            ["security-review", "security-review"],
            "security-review",
        )
        for selection in selections:
            with self.subTest(selection=selection), self.assertRaises(WeaverError):
                render_instruction_bundle(spec, "cli", self.profile, selection, ROOT)

    def test_unsupported_surface_and_gateway_fail_closed(self):
        kilo = get_integration("kilo", ROOT)
        with self.assertRaises(WeaverError):
            render_instruction_bundle(kilo, "not-real", self.profile, [], ROOT)
        gateway = get_integration("openrouter", ROOT)
        with self.assertRaises(WeaverError) as raised:
            render_instruction_bundle(gateway, "gateway", self.profile, [], ROOT)
        self.assertIn("provider", str(raised.exception).casefold())

    def test_rendering_is_deterministic_keeps_canonical_hashes_and_uses_safe_writer(self):
        before = {
            name: hashlib.sha256(skill_path(ROOT, name).read_bytes()).hexdigest()
            for name in EXPECTED_SKILLS
        }
        spec = get_integration("claude-code", ROOT)
        first = render_instruction_bundle(
            spec, "cli", self.profile, ["security-review", "benchmark-design"], ROOT
        )
        second = render_instruction_bundle(
            spec, "cli", self.profile, ["security-review", "benchmark-design"], ROOT
        )
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            write_artifacts(output, first)
            with self.assertRaises(WeaverError):
                write_artifacts(output, first)
            for artifact in first:
                self.assertEqual(
                    (output / artifact.relative_path).read_bytes(), artifact.content
                )
        after = {
            name: hashlib.sha256(skill_path(ROOT, name).read_bytes()).hexdigest()
            for name in EXPECTED_SKILLS
        }
        self.assertEqual(before, after)

    def test_rendering_performs_no_network_or_agent_execution(self):
        spec = get_integration("opencode", ROOT)
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("process used")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process used")):
            artifacts = render_instruction_bundle(
                spec, "cli", self.profile, ["security-review"], ROOT
            )
        self.assertTrue(artifacts)


if __name__ == "__main__":
    unittest.main()
