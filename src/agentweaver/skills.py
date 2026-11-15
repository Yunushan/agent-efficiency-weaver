import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from pprint import pformat
from urllib.parse import unquote

from . import __version__
from .artifacts import RenderedArtifact
from .config import WeaverError, safe_name, safe_text, select_servers, validate_profile
from .integrations import IntegrationSpec, SurfaceSpec

SKILLS_DIRECTORY = ".agents/skills"
SKILL_FILE = "SKILL.md"
CANONICAL_SKILL_COUNT = 6
CODEX_LIMIT_BYTES = 32 * 1024
ABACUS_LIMIT_CHARACTERS = 40000
_FIELD_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]+(.*))?$")
_PLUGIN_SCHEMAS = "https://agent-plugins.org/schemas/1.0.0"
_DEFAULT_CHATGPT_PLUGIN_NAME = "weaver-workflows"
_CHATGPT_PLUGIN_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_WINDOWS_DEVICE_NAME = re.compile(r"(?:aux|con|nul|prn|com[0-9]|lpt[0-9])")
_CHATGPT_PLUGIN_APP_ID = re.compile(r"(?:asdk_app_|connector_|templated_apps_)[A-Za-z0-9_-]+")
_MANUAL_SKILL_IMPORTS = {
    ("lm-studio", "bionic"): (
        "In Bionic, open Settings > Skills > Add Skill > Install a skill. "
        "Choose Install manually if prompted, then Select a file and review and install each staged SKILL.md. "
        "Skills from other apps require a separate visibility choice in Settings > Skills."
    ),
    ("perplexity", "app"): (
        "In Perplexity Computer, open Plugins > Add custom > Skill > Upload skill and upload "
        "each staged SKILL.md file. On accounts without Plugins, use Customize > Skills > Create skill."
    ),
    ("perplexity", "agent-api"): (
        "From imports/skills, run `python -m zipfile -c NAME.zip NAME` separately for each selected "
        "Skill directory. Upload each ZIP to the intended Project in the Perplexity API Portal. "
        "Use the resulting project-owned ID in the Agent API request's skills array with type "
        "custom. Set version to the reviewed revision to make the reference reproducible. "
        "Upload and execution require separate authorization."
    ),
}


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


def selected_portable_skill_ids(root: Path, skills: list[str]) -> tuple[str, ...]:
    """Resolve exact IDs against the same validated sources used by bundle."""
    return tuple(skill.name for skill in _validated_skill_selection(root, skills))


def _validated_skill_selection(root: Path, skills: list[str]) -> list[_Skill]:
    available, errors = _scan_skills(root)
    if errors:
        raise WeaverError("Skill validation failed: " + "; ".join(sorted(errors)))
    return _select_skills(skills, available)


def stage_portable_skills(root: Path, skills: list[str], prefix: PurePosixPath) -> list[RenderedArtifact]:
    return [
        RenderedArtifact(prefix / "skills" / skill.name / SKILL_FILE, skill.content, "skill")
        for skill in _validated_skill_selection(root, skills)
    ]


def portable_skill_texts(root: Path, skills: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    descriptions: dict[str, str] = {}
    instructions: dict[str, str] = {}
    for skill in _validated_skill_selection(root, skills):
        body = _skill_body(skill)
        if not body.strip():
            raise WeaverError("Portable Skill tools require non-empty instructions")
        descriptions[skill.name] = skill.description
        instructions[skill.name] = body
    return descriptions, instructions


def _plugin_json(path: PurePosixPath, document: dict, kind: str) -> RenderedArtifact:
    return RenderedArtifact(path, (json.dumps(document, indent=2) + "\n").encode(), kind)


def render_lmstudio_skill_mcp(
    spec: IntegrationSpec, surface: str, root: Path, profile: dict, skills: list[str], output_dir: Path,
) -> list[RenderedArtifact]:
    from .adapters import lmstudio_stdio_fragments
    from .skill_mcp import SERVER_NAME, TOOL_NAME

    if spec.id != "lm-studio" or surface not in {"desktop", "api"}:
        raise WeaverError("LM Studio Skill MCP staging requires classic Desktop or the native API")
    if not skills:
        raise WeaverError("Select at least one Skill to stage the Skill MCP server")
    if _profile_facts(profile).max_mcp_servers == 0:
        raise WeaverError("The profile has no MCP server capacity")
    prefix = PurePosixPath("lm-studio", "skills-mcp")
    server_path = prefix / "skill-server.py"
    descriptions, instructions = portable_skill_texts(root, skills)
    selected = {name: (description, instructions[name]) for name, description in descriptions.items()}
    contents = files("agentweaver").joinpath("skill_mcp.py").read_text(encoding="utf-8")
    embedded_skills = pformat(selected, width=92).replace("\n", "\n    ")
    contents += ("\n\nif __name__ == \"__main__\":\n"
                 "    import sys\n"
                 f"\n    SKILLS = {embedded_skills}\n"
                 "\n    serve(sys.stdin.buffer, sys.stdout.buffer, SKILLS)\n")
    configuration, request = lmstudio_stdio_fragments(
        SERVER_NAME, str(Path(sys.executable).absolute()),
        ["-I", str(output_dir.absolute().joinpath(*server_path.parts))], [TOOL_NAME],
    )
    artifacts = [
        RenderedArtifact(server_path, contents.encode("utf-8"), "skill-mcp-server"),
        _plugin_json(prefix / "mcp.fragment.json", configuration, "mcp-fragment"),
    ]
    notes = [
        "# LM Studio local Skill tool", "",
        "Review `skill-server.py` and the selected Skill instructions embedded in it.",
        "Keep the staged directory at its original location and use the Python executable recorded in the fragment.",
        "Merge only the `weaver-skills` entry from `mcp.fragment.json` into LM Studio's `mcp.json` when you want this tool.",
        "Choose a tool-capable local model. The tool advertises the selected Skills and returns the instructions for an exact name.",
        "The standalone process uses stdio. Its only result is the reviewed text of a selected Skill.",
        "Choose whether to expose those instructions before sharing a conversation with any remote model.",
        "[LM Studio MCP settings](https://lmstudio.ai/docs/app/mcp)",
    ]
    if surface == "api":
        artifacts.append(_plugin_json(prefix / "chat-integration.json", request, "mcp-request"))
        notes.extend([
            "",
            "Merge `chat-integration.json` into a request to `POST /api/v1/chat` on the intended LM Studio host.",
            "The server must permit calls to preconfigured MCP entries. Supply the model, input, and any server token separately.",
            "[Preconfigured API MCP servers](https://lmstudio.ai/docs/developer/core/mcp#mcp-servers-from-mcp-json)",
        ])
    artifacts.append(RenderedArtifact(prefix / "SETUP.md", ("\n".join(notes) + "\n").encode("utf-8"), "handoff"))
    return artifacts


def _portable_plugin_package(
    package: PurePosixPath, manifest: dict, servers: Mapping[str, Mapping[str, str | list[str]]], skills: list[_Skill],
) -> list[RenderedArtifact]:
    artifacts = [_plugin_json(package / "plugin.json",
                              {"$schema": f"{_PLUGIN_SCHEMAS}/plugin.schema.json", **manifest}, "plugin-manifest")]
    if servers:
        artifacts.append(_plugin_json(package / "mcp.json",
                                     {"$schema": f"{_PLUGIN_SCHEMAS}/mcp.schema.json", "mcpServers": servers},
                                     "plugin-mcp"))
    artifacts.extend(RenderedArtifact(package / "skills" / skill.name / SKILL_FILE, skill.content, "skill")
                     for skill in skills)
    return artifacts


def _package_zip_command(package: PurePosixPath, artifacts: list[RenderedArtifact]) -> str:
    files = [item.relative_path.relative_to(package).as_posix() for item in artifacts]
    script = ("from zipfile import ZipFile; "
              f"archive=ZipFile('../{package.name}.zip','x'); "
              f"[archive.write(path, path) for path in {files!r}]; archive.close()")
    return f'python -c "{script}"'


def render_perplexity_plugin(
    spec: IntegrationSpec, surface: str, root: Path, profile: dict, catalog: dict,
    skills: list[str], http: list[str], sse: list[str],
) -> list[RenderedArtifact]:
    target = spec.surface(surface)
    if (spec.id, target.id) != ("perplexity", "app") or any(
        target.capabilities[capability].state != "manual" for capability in ("skills", "mcp")
    ):
        raise WeaverError("Portable plugin staging requires the Perplexity app surface")
    selected = select_servers(profile, catalog, [*http, *sse])
    if not skills and not selected:
        raise WeaverError("Select at least one plugin Skill or MCP server")
    if len(selected) > 15:
        raise WeaverError("Perplexity plugin packages support up to 15 MCP servers")
    sse_ids = set(sse)
    servers = {}
    for name, url in selected.items():
        if not isinstance(url, str):
            raise WeaverError("Perplexity plugin packages accept remote HTTPS MCP connectors")
        if re.search(r"\$\{|\{\{|\{env:", unquote(url), flags=re.IGNORECASE):
            raise WeaverError("Perplexity plugin connectors require literal HTTPS URLs")
        servers[name] = {"type": "sse" if name in sse_ids else "streamable-http", "url": url}
    base = PurePosixPath(spec.id, "plugins")
    package = base / "weaver-workflows"
    manifest = {"name": package.name,
                "description": "Selected Agent Efficiency Weaver Skills and MCP connectors"}
    artifacts = _portable_plugin_package(package, manifest, servers, _validated_skill_selection(root, skills))
    if sum(len(item.content) for item in artifacts) > 20_000_000:
        raise WeaverError("Perplexity plugin package exceeds 20 MB uncompressed")
    handoff = (
        "# Perplexity plugin handoff\n\n"
        "Review the files in weaver-workflows. From inside that directory, create the upload ZIP.\n\n"
        f"```sh\n{_package_zip_command(package, artifacts)}\n```\n\n"
        "If the ZIP already exists, move it aside before packaging the reviewed files.\n\n"
        "In Perplexity, open Plugins > Add custom > Upload plugin or skill. Review the imported Skills and "
        "confirm every intended connector appears before connecting it. Perplexity checks access separately. "
        "Connectors inherit the account and organization policies. Supply any authentication through Perplexity.\n\n"
        "Weaver stages the package offline. You control the upload, connector setup and any later prompts.\n"
    )
    artifacts.append(RenderedArtifact(base / "PLUGIN-HANDOFF.md", handoff.encode(), "handoff"))
    return artifacts


def _chatgpt_app_references(selectors: list[str]) -> dict:
    references: dict = {}
    seen = set()
    for selector in selectors:
        alias, _, reference = selector.partition(":")
        app_id = reference.removeprefix("plugin_")
        if not safe_name(alias) or not _CHATGPT_PLUGIN_APP_ID.fullmatch(app_id):
            raise WeaverError("ChatGPT app references require NAME:ID with an existing app ID or its plugin_ form")
        if alias.casefold() in seen:
            raise WeaverError("Duplicate ChatGPT plugin app name")
        seen.add(alias.casefold())
        references[alias] = {"id": app_id, "required": True}
    return dict(sorted(references.items()))


def render_chatgpt_plugin(
    spec: IntegrationSpec, surface: str, root: Path, profile: dict, catalog: dict,
    skills: list[str], enabled: list[str], *, workspace_marketplace: bool = False,
    app_selectors: list[str] | None = None, plugin_name: str | None = None,
) -> list[RenderedArtifact]:
    target = spec.surface(surface)
    if spec.id != "chatgpt" or surface not in {"desktop", "web", "work-local", "work-cloud"} or any(
        target.capabilities[capability].state != "manual" for capability in ("skills", "mcp")
    ):
        raise WeaverError("ChatGPT plugin staging requires a registered ChatGPT or Work surface")
    if plugin_name is None:
        plugin_name = _DEFAULT_CHATGPT_PLUGIN_NAME
    if (len(plugin_name) > 64 or not _CHATGPT_PLUGIN_NAME.fullmatch(plugin_name)
            or _WINDOWS_DEVICE_NAME.fullmatch(plugin_name)):
        raise WeaverError("ChatGPT plugin name must be a portable lowercase ID of up to 64 letters, digits, and hyphens")
    selected = select_servers(profile, catalog, enabled)
    local = surface in {"desktop", "work-local"}
    apps = _chatgpt_app_references(app_selectors or [])
    if apps and not (local or workspace_marketplace):
        raise WeaverError("Hosted ChatGPT app references require --chatgpt-workspace-marketplace")
    if not (skills or selected or apps):
        raise WeaverError("Select at least one ChatGPT plugin Skill, MCP server, or existing app")
    servers: dict[str, dict[str, str | list[str]]] = {}
    requires_local_executor = False
    for name, entry in selected.items():
        if isinstance(entry, str):
            if re.search(r"\$\{|\{\{|\{env:", unquote(entry), flags=re.IGNORECASE):
                raise WeaverError("ChatGPT plugin MCP URLs must be literal HTTPS URLs")
            servers[name] = {"type": "streamable-http", "url": entry}
        else:
            if not local:
                raise WeaverError("ChatGPT plugin stdio servers require the Desktop or Work Local surface")
            command = entry["command"]
            if ("\\" in command or ":" in command or command in {".", ".."}
                    or ("/" in command and (not command.startswith("./")
                        or set(command[2:].split("/")) & {"", ".", ".."}))):
                raise WeaverError("ChatGPT plugin stdio requires a bare executable name or a safe ./ package path")
            requires_local_executor = True
            servers[name] = {"type": "stdio", "command": command, "args": entry["args"]}
    chosen_skills = _validated_skill_selection(root, skills)
    selection = {"servers": servers, "apps": apps, "workspace_marketplace": workspace_marketplace,
                 "skills": {skill.name: hashlib.sha256(skill.content).hexdigest() for skill in chosen_skills}}
    if plugin_name != _DEFAULT_CHATGPT_PLUGIN_NAME:
        selection["plugin_name"] = plugin_name
    selection_hash = hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()[:16]
    base = PurePosixPath(spec.id, "plugin-staging", surface, selection_hash)
    package = base / "plugins" / plugin_name
    manifest: dict = {"name": package.name, "version": __version__,
                      "description": "Selected Agent Efficiency Weaver Skills and MCP connectors"}
    openai: dict = {}
    if requires_local_executor:
        openai["requires_local_executor"] = True
    if apps:
        openai["apps"] = "./.app.json"
    if openai:
        manifest["extensions"] = {"com.openai": openai}
    artifacts = _portable_plugin_package(package, manifest, servers, chosen_skills)
    if apps:
        artifacts.append(_plugin_json(package / ".app.json", {"apps": apps}, "plugin-apps"))
    if local or workspace_marketplace:
        marketplace = {"name": "weaver-workspace" if workspace_marketplace else "weaver-local",
                       "interface": {"displayName": "Weaver Workflows"},
                       "plugins": [{"name": package.name, "source": {"source": "local",
                                                                    "path": f"./plugins/{package.name}"},
                                    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                                    "category": "Productivity"}]}
        artifacts.append(_plugin_json(base / ".agents/plugins/marketplace.json", marketplace, "plugin-marketplace"))
    handoff = [
        "# ChatGPT plugin handoff", "",
        f"Target surface: `chatgpt/{surface}`.", "",
        "Review the selected Skills and MCP entries before using the package.",
    ]
    if local:
        handoff.extend([
            "", "For local discovery, review the staged marketplace entry in `.agents/plugins/marketplace.json`. "
            f"Place the selected package in a fresh `plugins/{plugin_name}` directory in the destination repo "
            "and merge the entry into its marketplace. Preserve other entries. Restart the desktop app, open the "
            "repo's marketplace, and review the plugin before choosing whether to install it.",
        ])
    if workspace_marketplace:
        handoff.extend([
            "", "For workspace import, place the reviewed marketplace and plugin directory in the intended "
            "GitHub repository. A workspace admin opens Admin > Plugins > Add > Import marketplace and "
            "selects the directory containing .agents/plugins/marketplace.json. Choose the reviewed commit "
            "when updates should be pinned. Review the saved import report and choose access in the admin UI. "
            "Workspace installation and authentication policies are configured there.", "",
            "[Workspace GitHub plugin import](https://learn.chatgpt.com/docs/enterprise/plugin-management)",
        ])
        if servers:
            handoff.append("Direct MCP declarations can cause GitHub-imported plugins to be marked Desktop only.")
    if apps:
        handoff.extend([
            "", "Review the required app references in .app.json. Each ID refers to an app already registered "
            "in the destination workspace. Verify role access and authentication for each app. "
            "Use the marketplace workflow for this package.",
        ])
    else:
        package_files = [item for item in artifacts if item.relative_path.is_relative_to(package)]
        handoff.extend([
            "", f"For the OpenAI Platform submission workflow, run this command inside `plugins/{plugin_name}` "
            "to ZIP only the selected package files. Move aside any existing ZIP first.", "",
            "```sh", _package_zip_command(package, package_files), "```", "",
            "Use the documented submission type for the reviewed package. With MCP uses public HTTPS and "
            "requires domain verification and separate authentication setup where applicable. Only one declared "
            "MCP server can be connected per plugin through the submission portal.",
        ])
    handoff.append("")
    if requires_local_executor:
        handoff.extend([
            "For local testing, make bare executable names available on PATH and place any ./ executable "
            "under the plugin root. Add required runtime dependencies separately. Public desktop-only "
            "submission requires prior approval and packaging the server and dependencies with the plugin.", "",
            "[Desktop-only submissions](https://developers.openai.com/plugins/deploy/desktop-only)",
        ])
    handoff.extend([
        "[Package your plugin](https://developers.openai.com/plugins/build/plugins)",
        "[OpenAI Platform submission](https://developers.openai.com/plugins/deploy/submission)", "",
    ])
    artifacts.append(RenderedArtifact(base / "PLUGIN-HANDOFF.md", "\n".join(handoff).encode(), "handoff"))
    return artifacts


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


def _manual_skill_import(spec: IntegrationSpec, surface: SurfaceSpec) -> str | None:
    evidence = surface.capabilities["skills"]
    allowed_states = {"manual", "available"} if (spec.id, surface.id) == ("perplexity", "agent-api") else {"manual"}
    if evidence.state not in allowed_states or not evidence.sources:
        return None
    return _MANUAL_SKILL_IMPORTS.get((spec.id, surface.id))


def _skill_body(skill: _Skill) -> str:
    return skill.content.decode("utf-8").partition("\n---\n")[2].removeprefix("\n")


def _perplexity_inline_fragment(spec: IntegrationSpec, selected: list[_Skill]) -> RenderedArtifact:
    if not 1 <= len(selected) <= 16:
        raise WeaverError("Select one to sixteen Perplexity inline Skills")
    entries = []
    total_bytes = 0
    for skill in selected:
        if len(skill.name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill.name):
            raise WeaverError("Invalid Perplexity inline Skill name")
        instructions = _skill_body(skill)
        size = len(instructions.encode("utf-8"))
        if not instructions.strip() or size > 65_536:
            raise WeaverError("Perplexity inline Skill instructions must have content and fit within 65,536 UTF-8 bytes")
        if len(skill.description.encode("utf-8")) > 1024:
            raise WeaverError("Perplexity inline Skill description exceeds 1,024 UTF-8 bytes")
        total_bytes += size
        entries.append({"type": "inline", "name": skill.name, "description": skill.description,
                        "instructions": instructions})
    if total_bytes > 262_144:
        raise WeaverError("Perplexity inline Skill instructions exceed 262,144 UTF-8 bytes for the request")
    return RenderedArtifact(PurePosixPath(spec.id, "requests", "agent-api.skills.json"),
                            (json.dumps({"skills": entries}, indent=2) + "\n").encode(), "skill-request")


def _lmstudio_preset(facts: _ProfileFacts, thinking: str | None, budget: int | None) -> RenderedArtifact:
    prompt = [
        f"Apply the {facts.identifier} profile as advisory guidance.",
        f"Read at most {facts.max_snippets} relevant retrieval snippets.",
        "Keep production writes disabled.",
    ]
    if facts.avoid_full_repo_dumps:
        prompt.append("Avoid full-repository dumps.")
    fields: list[dict[str, object]] = [{"key": "llm.prediction.systemPrompt", "value": "\n".join(prompt)}]
    if thinking is not None:
        if thinking not in {"on", "off"}:
            raise WeaverError("LM Studio preset thinking must be on or off")
        fields.append({"key": "llm.prediction.reasoning.enableThinking", "value": thinking == "on"})
    if budget is not None:
        if type(budget) is not int or not 0 <= budget <= 2**53 - 1:
            raise WeaverError("LM Studio preset reasoning budget must be a nonnegative JavaScript safe integer")
        fields.append({"key": "llm.prediction.reasoning.budgetTokens", "value": {"checked": True, "value": budget}})
    slug = f"weaver-{facts.identifier}"
    payload = {"identifier": f"@local:{slug}", "name": f"Agent Efficiency Weaver ({facts.identifier})",
               "operation": {"fields": fields}, "load": {"fields": []}}
    return RenderedArtifact(PurePosixPath("lm-studio", "imports", "presets", f"{slug}.preset.json"),
                            (json.dumps(payload, indent=2) + "\n").encode("utf-8"), "preset-import")


def _instruction_text(
    spec: IntegrationSpec,
    surface: SurfaceSpec,
    facts: _ProfileFacts,
    selected: list[_Skill],
    manual: bool,
    native_skills_path: str | None,
    manual_skill_import: str | None,
    inline_skills: RenderedArtifact | None,
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
    if (spec.id, surface.id) == ("copilot", "desktop"):
        lines.extend(["", "Open Settings > Repositories, select the repository, and review this content for "
                      "its Instructions field. Use Customize > Skills to review project Skill discovery in a "
                      "local repository session. App-wide instructions are a separate setting."])
    if spec.id == "jetbrains-air" and surface.id == "ide":
        lines.extend([
            "", "In Air in IDEs, open Manage Agents > Manage Presets and create a New Preset. "
            "Choose an already installed agent, expand Advanced, and review this advisory content "
            "for the Injected instructions field. Saving a preset affects subsequent sessions. "
            "Model, effort, mode and authorization remain choices of the selected agent and account.",
            "Review the shared .agents/skills files under AI > Skills. Start a new session and use "
            "Add > Skills to choose an available project Skill. Agent and IDE settings govern availability. "
            "An ACP executable registration is separate from a preset and a subagent definition.",
        ])
    if spec.id == "chatgpt":
        lines.extend(
            [
                "",
                "ChatGPT local Work and hosted Work are separate surfaces. Apply this handoff "
                "through the selected surface's documented controls. Hosted Work does not read "
                "local .codex configuration, and this bundle claims no automatic repository discovery.",
            ]
        )
    if spec.id == "lm-studio" and surface.id == "desktop":
        lines.extend([
            "", "Apply the advisory instructions as a system prompt through LM Studio's Preset "
            "controls. This handoff is not a Preset JSON file or an AGENTS.md discovery claim. "
            "Bionic is a separate app; its Skill import behavior is not assigned to LM Studio classic.",
        ])
    if spec.id == "lm-studio" and surface.id in {"desktop", "api"}:
        lines.append("For on-demand Skill text through the local MCP host, use `bundle --lmstudio-skill-mcp`.")
    if spec.id == "perplexity" and surface.id == "app":
        lines.extend([
            "", "Apply the advisory instructions in Perplexity Project settings. Project instructions "
            "are limited to 8,000 characters. Skill import below is specific to Perplexity Computer; "
            "this bundle does not configure the Orchestrator API.",
        ])
    if spec.id == "perplexity" and surface.id == "orchestrator":
        lines.extend([
            "", "Apply the advisory instructions through the selected Orchestrator request's prompt "
            "or message fields. For on-demand loading, stage a model request with one or more "
            "`--skill` selections and use the generated client-side Skill tool. Staged read files "
            "can also be supplied manually as context.",
        ])
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
    elif (spec.id, surface.id) == ("antigravity", "sdk"):
        lines.extend(["Skills are supplied through explicit SDK paths.",
                      *[f"- {skill.name}: {skill.description}" for skill in selected]])
    elif inline_skills is not None:
        lines.extend([
            f"Review the selected inline Skills in `{inline_skills.relative_path.as_posix()}` before adding "
            "the skills array to a `POST /v1/agent` request.",
            "Review the complete request for duplicate Skill names and existing Skill references. "
            "Sending a request with any Skill enables Perplexity's request sandbox. "
            "Inline Skills contain only descriptions and instructions.",
            "With an explicit model or fallback chain and no effort preset, the API defaults to one step. "
            "Use `model --perplexity-max-steps` to budget at least two steps for loading and using a Skill.",
            "- https://docs.perplexity.ai/docs/agent-api/skills",
            *[f"- {skill.name}: {skill.description}" for skill in selected],
        ])
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
    elif manual_skill_import is not None:
        lines.extend([
            "Skills: manual vendor import. Selected canonical files retain the standard SKILL.md "
            "filename and LF-normalized content under imports/skills/. No app settings are changed "
            "and this staging directory is not a native discovery path.",
            manual_skill_import,
            "Sources:",
            *[f"- {url}" for url in surface.capabilities["skills"].sources],
            *[f"- {skill.name}: imports/skills/{skill.name}/{SKILL_FILE}" for skill in selected],
        ])
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
    *,
    lmstudio_preset: bool = False,
    lmstudio_thinking: str | None = None,
    lmstudio_budget: int | None = None,
    perplexity_inline_skills: bool = False,
) -> list[RenderedArtifact]:
    if not isinstance(spec, IntegrationSpec):
        raise WeaverError("Integration specification required")
    if spec.kind not in {"agent", "hybrid"}:
        raise WeaverError("Provider gateways cannot render instruction bundles; use the provider command")
    if lmstudio_preset and (spec.id, surface) != ("lm-studio", "desktop"):
        raise WeaverError("LM Studio preset staging requires the classic Desktop target")
    if not lmstudio_preset and (lmstudio_thinking is not None or lmstudio_budget is not None):
        raise WeaverError("LM Studio preset reasoning controls require --lmstudio-preset")
    target = spec.surface(surface)
    if perplexity_inline_skills and (
        (spec.id, surface) != ("perplexity", "agent-api") or target.config_scope != "request"
        or target.capabilities["skills"].state != "available" or not target.capabilities["skills"].sources
    ):
        raise WeaverError("Inline Skills require the Perplexity Agent API request surface")
    facts = _profile_facts(profile)
    selected = _validated_skill_selection(Path(root), skills)
    inline_skills = _perplexity_inline_fragment(spec, selected) if perplexity_inline_skills else None
    instruction_evidence = target.capabilities["instructions"]
    if instruction_evidence.state in {"unsupported", "unknown"}:
        raise WeaverError(
            f"Instruction bundle unavailable for {spec.id}/{surface}: "
            f"evidence state is {instruction_evidence.state}"
        )
    if not instruction_evidence.sources:
        raise WeaverError("Instruction bundle requires documented source evidence")
    if (spec.id, surface) == ("antigravity", "sdk"):
        from .antigravity_sdk import SDK_PREFIX, render_sdk_context

        skill_files = [RenderedArtifact(SDK_PREFIX / "context" / "skills" / skill.name / SKILL_FILE,
                                        skill.content, "skill") for skill in selected]
        guidance = _instruction_text(spec, target, facts, selected, False, None, None, None)
        return render_sdk_context(guidance, skill_files)
    instruction_target = _instruction_target(spec, target)
    manual = instruction_evidence.state != "available" or instruction_target is None
    native_skills_path = _native_skills_path(spec, target)
    manual_skill_import = _manual_skill_import(spec, target)
    content = _instruction_text(
        spec, target, facts, selected, manual, native_skills_path, manual_skill_import, inline_skills,
    )
    preset = None
    if lmstudio_preset:
        preset = _lmstudio_preset(facts, lmstudio_thinking, lmstudio_budget)
        content += (
            "\n## LM Studio Preset import\n"
            f"Review `{preset.relative_path.as_posix()}`. Open the Presets dropdown in LM Studio, "
            "choose Import, and select the file. Review the system prompt before applying it. "
            "Choose the model separately and check whether it supports any configured thinking or budget setting. "
            "The preset supplies advisory instructions and prediction settings.\n"
        )
    _check_instruction_limit(spec, content)
    if manual or instruction_target is None:
        relative_path = PurePosixPath(spec.id, "handoff", "INSTRUCTIONS.md")
        kind = "handoff"
    else:
        relative_path = PurePosixPath(spec.id, instruction_target)
        kind = "instructions"
    artifacts = [RenderedArtifact(relative_path, content.encode("utf-8"), kind)]
    if preset is not None:
        artifacts.append(preset)
    if inline_skills is not None:
        artifacts.append(inline_skills)
    elif native_skills_path is None and manual_skill_import is not None:
        for skill in selected:
            artifacts.append(
                RenderedArtifact(
                    PurePosixPath(spec.id, "imports", "skills", skill.name, SKILL_FILE),
                    skill.content,
                    "skill-import",
                )
            )
    elif native_skills_path is None:
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
