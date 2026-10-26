import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .artifacts import RenderedArtifact
from .config import WeaverError, safe_name, safe_text, validate_profile
from .integrations import IntegrationSpec, SurfaceSpec

SKILLS_DIRECTORY = ".agents/skills"
SKILL_FILE = "SKILL.md"
CANONICAL_SKILL_COUNT = 6
CODEX_LIMIT_BYTES = 32 * 1024
ABACUS_LIMIT_CHARACTERS = 40000
_FIELD_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]+(.*))?$")


@dataclass(frozen=True)
class _Skill:
    name: str
    description: str
    content: bytes


@dataclass(frozen=True)
class _ProfileFacts:
    identifier: str
    description: str
    max_mcp_servers: int
    allowed_mcp: tuple[str, ...]
    max_snippets: int
    avoid_full_repo_dumps: bool


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise WeaverError("Unable to inspect skills path") from exc


def _is_link(item: os.stat_result) -> bool:
    if stat.S_ISLNK(item.st_mode):
        return True
    attributes = getattr(item, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _control_character(text: str, *, allow_layout: bool) -> str | None:
    for character in text:
        if allow_layout and character in {"\n", "\t"}:
            continue
        code = ord(character)
        if code < 32 or code == 127 or 128 <= code <= 159:
            return character
        if unicodedata.category(character) in {"Cc", "Cs"}:
            return character
    return None


def _safe_scalar(value: str) -> tuple[str | None, str | None]:
    if _control_character(value, allow_layout=False) is not None:
        return None, "control character in front matter value"
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError, UnicodeError):
            return None, "malformed quoted front matter value"
        if not isinstance(parsed, str):
            return None, "front matter value must be a string"
    elif value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            return None, "malformed quoted front matter value"
        inner = value[1:-1]
        if "'" in inner.replace("''", ""):
            return None, "malformed quoted front matter value"
        parsed = inner.replace("''", "'")
    else:
        if value.endswith(("'", '"')):
            return None, "malformed quoted front matter value"
        parsed = value
    if not parsed or not parsed.strip():
        return None, "front matter value must not be empty"
    if not safe_text(parsed):
        return None, "invalid character in front matter value"
    return parsed, None


def _normalize_newlines(text: str) -> str | None:
    normalized = text.replace("\r\n", "\n")
    if "\r" in normalized:
        return None
    return normalized


def _parse_front_matter(text: str) -> tuple[str | None, str | None, list[str]]:
    errors = []
    control = _control_character(text, allow_layout=True)
    if control is not None:
        return None, None, ["control character in skill file"]
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return None, None, ["skill file must start with YAML front matter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None, None, ["skill front matter is not terminated"]
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            errors.append("malformed blank line in front matter")
            continue
        match = _FIELD_LINE.fullmatch(line)
        if match is None:
            errors.append("malformed front matter field")
            continue
        key = match.group(1)
        raw = match.group(2)
        if key not in {"name", "description"}:
            errors.append(f"unknown front matter field {key!r}")
            continue
        if key in values:
            errors.append(f"duplicate front matter field {key!r}")
            continue
        if raw is None or not raw.strip():
            errors.append(f"missing front matter field {key!r}")
            continue
        parsed, problem = _safe_scalar(raw.strip())
        if problem is not None or parsed is None:
            errors.append(f"{key}: {problem or 'invalid value'}")
            continue
        values[key] = parsed
    name = values.get("name")
    description = values.get("description")
    if name is None:
        errors.append("missing required front matter field 'name'")
    elif not safe_name(name):
        errors.append("invalid skill name in front matter")
    if description is None:
        errors.append("missing required front matter field 'description'")
    return name, description, errors


def _scan_skills(root: Path) -> tuple[dict[str, _Skill], list[str]]:
    skills_directory = Path(root) / SKILLS_DIRECTORY
    try:
        directory_state = _lstat(skills_directory)
    except WeaverError as exc:
        return {}, [str(exc)]
    if directory_state is None:
        return {}, ["missing canonical skills directory .agents/skills"]
    if _is_link(directory_state) or not stat.S_ISDIR(directory_state.st_mode):
        return {}, ["canonical skills directory must be a real directory"]
    try:
        candidates = sorted(skills_directory.iterdir(), key=lambda item: item.name.casefold())
    except (OSError, ValueError) as exc:
        raise WeaverError("Unable to list canonical skills directory") from exc

    errors: list[str] = []
    folders: list[Path] = []
    for candidate in candidates:
        try:
            item_state = _lstat(candidate)
        except WeaverError as exc:
            errors.append(str(exc))
            continue
        if item_state is None:
            errors.append(f"skill entry disappeared: {candidate.name}")
            continue
        if _is_link(item_state):
            errors.append(f"skill entry must not be a link: {candidate.name}")
            continue
        if not stat.S_ISDIR(item_state.st_mode):
            errors.append(f"unexpected entry in canonical skills directory: {candidate.name}")
            continue
        if not safe_name(candidate.name):
            errors.append(f"invalid skill folder name: {candidate.name}")
            continue
        folders.append(candidate)
    if len(folders) != CANONICAL_SKILL_COUNT:
        errors.append(
            f"canonical skills directory must contain exactly six skill folders, found {len(folders)}"
        )

    skills: dict[str, _Skill] = {}
    seen_names: dict[str, str] = {}
    for folder in folders:
        path = folder / SKILL_FILE
        try:
            file_state = _lstat(path)
        except WeaverError as exc:
            errors.append(str(exc))
            continue
        if file_state is None:
            errors.append(f"missing {SKILL_FILE} in skill folder: {folder.name}")
            continue
        if _is_link(file_state):
            errors.append(f"skill file must not be a link: {folder.name}/{SKILL_FILE}")
            continue
        if not stat.S_ISREG(file_state.st_mode):
            errors.append(f"skill file must be a regular file: {folder.name}/{SKILL_FILE}")
            continue
        try:
            content = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise WeaverError(f"Unable to read skill file: {folder.name}") from exc
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"skill file must be valid UTF-8: {folder.name}/{SKILL_FILE}")
            continue
        normalized = _normalize_newlines(text)
        if normalized is None:
            errors.append(f"skill file must use LF or CRLF line endings: {folder.name}/{SKILL_FILE}")
            continue
        name, description, problems = _parse_front_matter(normalized)
        errors.extend(f"{folder.name}: {problem}" for problem in problems)
        if name is None or description is None or problems:
            continue
        if name != folder.name:
            errors.append(
                f"skill folder {folder.name!r} does not match front matter name {name!r}"
            )
            continue
        key = name.casefold()
        if key in seen_names:
            errors.append(f"duplicate skill name: {name}")
            continue
        seen_names[key] = name
        skills[name] = _Skill(
            name=name,
            description=description,
            content=normalized.encode("utf-8"),
        )
    return skills, errors


def validate_skills(root: Path) -> list[str]:
    _, errors = _scan_skills(Path(root))
    return sorted(errors)


def _profile_facts(profile: dict) -> _ProfileFacts:
    if not isinstance(profile, dict):
        raise WeaverError("Profile must be an object")
    allowed_value = profile.get("allowed_mcp")
    synthetic_servers: dict[str, dict] = {}
    if isinstance(allowed_value, list) and all(
        isinstance(name, str) and safe_name(name) for name in allowed_value
    ):
        synthetic_servers = {name: {} for name in allowed_value}
    errors = validate_profile(profile, {"servers": synthetic_servers})
    if errors:
        raise WeaverError("; ".join(errors))
    if profile.get("id") == "enterprise-secure" and (
        profile.get("max_mcp_servers") != 0 or profile.get("allowed_mcp")
    ):
        raise WeaverError("enterprise-secure profile must keep MCP disabled")
    identifier = profile.get("id")
    description = profile.get("description", "")
    max_mcp_servers = profile.get("max_mcp_servers")
    allowed_mcp = profile.get("allowed_mcp")
    retrieval = profile.get("retrieval")
    if (
        not isinstance(identifier, str)
        or not isinstance(description, str)
        or type(max_mcp_servers) is not int
        or not isinstance(allowed_mcp, list)
        or not isinstance(retrieval, dict)
    ):
        raise WeaverError("Invalid profile")
    max_snippets = retrieval["max_snippets"]
    avoid_full_repo_dumps = retrieval["avoid_full_repo_dumps"]
    if type(max_snippets) is not int or type(avoid_full_repo_dumps) is not bool:
        raise WeaverError("Invalid retrieval policy")
    return _ProfileFacts(
        identifier=identifier,
        description=description,
        max_mcp_servers=max_mcp_servers,
        allowed_mcp=tuple(allowed_mcp),
        max_snippets=max_snippets,
        avoid_full_repo_dumps=avoid_full_repo_dumps,
    )


def _select_skills(skills: list[str], available: dict[str, _Skill]) -> list[_Skill]:
    if not isinstance(skills, list):
        raise WeaverError("Skill selection must be a list of skill names")
    seen = set()
    selected = []
    for name in skills:
        if not safe_name(name):
            raise WeaverError("Invalid skill selection")
        if name in seen:
            raise WeaverError("Duplicate skill selection")
        seen.add(name)
        try:
            selected.append(available[name])
        except KeyError:
            raise WeaverError(f"Unknown or invalid selected skill: {name}") from None
    return sorted(selected, key=lambda item: item.name)


def _instruction_target(spec: IntegrationSpec, surface: SurfaceSpec) -> str | None:
    if spec.id == "abacus":
        if "AGENTS.md" not in surface.instruction_paths:
            raise WeaverError("Abacus instruction export requires the documented root AGENTS.md")
        return "AGENTS.md"
    for path in surface.instruction_paths:
        if not path.endswith("/"):
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts or "\\" in path:
                raise WeaverError("Unsafe instruction path in integration registry")
            return path
    return None


def _native_skills_path(spec: IntegrationSpec, surface: SurfaceSpec) -> str | None:
    if spec.id == "abacus":
        return None
    evidence = surface.capabilities["skills"]
    if evidence.state != "available" or not surface.skills_path:
        return None
    path = PurePosixPath(surface.skills_path)
    if path.is_absolute() or ".." in path.parts or "\\" in surface.skills_path:
        raise WeaverError("Unsafe skills path in integration registry")
    return surface.skills_path


def _instruction_text(
    spec: IntegrationSpec,
    surface: SurfaceSpec,
    facts: _ProfileFacts,
    selected: list[_Skill],
    manual: bool,
    native_skills_path: str | None,
) -> str:
    allowed = ", ".join(facts.allowed_mcp) if facts.allowed_mcp else "none"
    lines = [
        "# Agent Efficiency Weaver instruction bundle",
        "",
        f"Agent: {spec.name} ({spec.id})",
        f"Surface: {surface.id}",
        f"Profile: {facts.identifier} - {facts.description}",
        "",
        "## Advisory profile guidance",
        "This guidance is advisory only. It is not tool enforcement, a sandbox, or a permission grant. "
        "Ambient configuration is not inspected, and global agent settings are outside this bundle.",
        "Infrastructure prerequisites are retained as external requirements; this bundle grants no OS, "
        "network, secret, or infrastructure access.",
        f"- MCP: at most {facts.max_mcp_servers} server(s); profile allowlist: {allowed}.",
        "- MCP configuration is not included in this bundle; use the documented MCP export.",
        f"- Retrieval: at most {facts.max_snippets} snippets; full-repository dumps "
        f"{'are avoided' if facts.avoid_full_repo_dumps else 'are not prohibited'}.",
        "- Production writes: disabled by profile policy.",
    ]
    if manual:
        lines.extend(
            [
                "",
                "## Manual handoff",
                "No documented project instruction file target is available for this surface, so no "
                "native path has been guessed. Apply this content through the vendor-supported manual "
                "mechanism and follow the first-party sources below.",
                "Sources:",
                *[f"- {url}" for url in surface.capabilities["instructions"].sources],
            ]
        )
    if spec.id == "chatgpt":
        lines.extend(
            [
                "",
                "ChatGPT local Work and hosted Work are separate surfaces. Apply this handoff "
                "through the selected surface's documented controls. Hosted Work does not read "
                "local .codex configuration, and this bundle claims no automatic repository discovery.",
            ]
        )
    if spec.id == "aider":
        read_paths = [
            "CONVENTIONS.md",
            *[f"read/{skill.name}.md" for skill in selected],
        ]
        command = "aider " + " ".join(f"--read {path}" for path in read_paths)
        lines.extend(
            [
                "",
                "## Aider read-file guidance",
                "CONVENTIONS.md is the documented conventions file. The selected skill files are "
                "staged as explicit read files under read/ after this bundle is copied into the "
                "project. Load them explicitly:",
                f"`{command}`",
                "Aider has no documented skill directory in this release. These read files are a "
                "manual projection, not an installed discovery mechanism.",
            ]
        )
    lines.extend(["", "## Skills"])
    if not selected:
        lines.append("Skills: none selected.")
    elif native_skills_path is not None:
        lines.append(
            f"Skills: selected for native discovery under {native_skills_path} after manual project import. "
            "Canonical LF-normalized content is copied without inlining here."
        )
        for skill in selected:
            lines.append(
                f"- {skill.name}: {skill.description} "
                f"(project path: {native_skills_path}/{skill.name}/{SKILL_FILE})"
            )
        if spec.id == "hermes":
            lines.append(
                "Hermes project Skills require an explicit `hermes skills trust` decision before "
                "discovery. This bundle does not grant project trust or modify trusted_project_dirs."
            )
    else:
        lines.append(
            "Skills: manual read-file projection. Selected canonical files are staged under "
            "read/ with LF-normalized content; they are not native discovery, and no native "
            "on-demand discovery is claimed."
        )
        for skill in selected:
            lines.append(
                f"- {skill.name}: {skill.description} (read file: read/{skill.name}.md)"
            )
    return "\n".join(lines) + "\n"


def _check_instruction_limit(spec: IntegrationSpec, content: str) -> None:
    if spec.id == "codex" and len(content.encode("utf-8")) > CODEX_LIMIT_BYTES:
        raise WeaverError(
            "Codex AGENTS.md exceeds the documented 32 KiB default instruction limit; "
            "refusing to truncate content"
        )
    if spec.id == "abacus" and len(content) > ABACUS_LIMIT_CHARACTERS:
        raise WeaverError(
            "Abacus AGENTS.md exceeds the documented 40,000 character instruction limit; "
            "refusing to truncate content"
        )


def render_instruction_bundle(
    spec: IntegrationSpec,
    surface: str,
    profile: dict,
    skills: list[str],
    root: Path,
) -> list[RenderedArtifact]:
    if not isinstance(spec, IntegrationSpec):
        raise WeaverError("Integration specification required")
    if spec.kind not in {"agent", "hybrid"}:
        raise WeaverError("Provider gateways cannot render instruction bundles; use the provider command")
    target = spec.surface(surface)
    facts = _profile_facts(profile)
    available, errors = _scan_skills(Path(root))
    if errors:
        raise WeaverError("Skill validation failed: " + "; ".join(sorted(errors)))
    selected = _select_skills(skills, available)
    instruction_evidence = target.capabilities["instructions"]
    if instruction_evidence.state in {"unsupported", "unknown"}:
        raise WeaverError(
            f"Instruction bundle unavailable for {spec.id}/{surface}: "
            f"evidence state is {instruction_evidence.state}"
        )
    if not instruction_evidence.sources:
        raise WeaverError("Instruction bundle requires documented source evidence")
    instruction_target = _instruction_target(spec, target)
    manual = instruction_evidence.state != "available" or instruction_target is None
    native_skills_path = _native_skills_path(spec, target)
    content = _instruction_text(
        spec, target, facts, selected, manual, native_skills_path
    )
    _check_instruction_limit(spec, content)
    if manual or instruction_target is None:
        relative_path = PurePosixPath(spec.id, "handoff", "INSTRUCTIONS.md")
        kind = "handoff"
    else:
        relative_path = PurePosixPath(spec.id, instruction_target)
        kind = "instructions"
    artifacts = [RenderedArtifact(relative_path, content.encode("utf-8"), kind)]
    if native_skills_path is None:
        for skill in selected:
            artifacts.append(
                RenderedArtifact(
                    PurePosixPath(spec.id, "read", f"{skill.name}.md"),
                    skill.content,
                    "read-file",
                )
            )
    else:
        for skill in selected:
            artifacts.append(
                RenderedArtifact(
                    PurePosixPath(
                        spec.id, native_skills_path, skill.name, SKILL_FILE
                    ),
                    skill.content,
                    "skill",
                )
            )
    return artifacts
