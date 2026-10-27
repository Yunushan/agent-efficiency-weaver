"""Setuptools build hook for the canonical public resources."""
import shutil
from pathlib import Path, PurePosixPath

from setuptools.command.build_py import build_py

ASSET_PATHS: tuple[str, ...] = (
    ".agents/skills/benchmark-design/SKILL.md",
    ".agents/skills/gitlab-ci-review/SKILL.md",
    ".agents/skills/linux-incident-triage/SKILL.md",
    ".agents/skills/rke2-troubleshooting/SKILL.md",
    ".agents/skills/security-review/SKILL.md",
    ".agents/skills/token-efficient-retrieval/SKILL.md",
    "catalogs/custom-agents.json",
    "catalogs/integrations.json",
    "catalogs/mcp-servers.json",
    "catalogs/model-hosts.json",
    "catalogs/model-providers.json",
    "catalogs/operating-systems.json",
    "catalogs/requested-coverage.json",
    "profiles/balanced.json",
    "profiles/efficient.json",
    "profiles/enterprise-secure.json",
)

SOURCE_ROOT = Path(__file__).resolve().parent


def asset_paths() -> tuple[str, ...]:
    return ASSET_PATHS


def copy_public_assets(source_root: str | Path, build_lib: str | Path) -> tuple[Path, ...]:
    source = Path(source_root).resolve()
    output = Path(build_lib).resolve()
    destination = output / "agentweaver" / "_data"
    source_tree = (source / "src").resolve()
    try:
        destination.relative_to(source_tree)
    except ValueError:
        pass
    else:
        raise RuntimeError("Refusing to write bundled assets into the source tree")
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise RuntimeError("Refusing to replace non-directory build assets")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for relative in ASSET_PATHS:
        source_file = source.joinpath(*PurePosixPath(relative).parts)
        if source_file.is_symlink() or not source_file.is_file():
            raise RuntimeError(f"Missing canonical resource: {relative}")
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
        copied.append(target)
    return tuple(copied)


class DataBuildPy(build_py):
    def run(self) -> None:
        super().run()
        copy_public_assets(SOURCE_ROOT, self.build_lib)
