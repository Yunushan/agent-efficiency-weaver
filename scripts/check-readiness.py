#!/usr/bin/env python3
"""Run the six documented offline release gates and emit a scoped JSON score."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES = (
    ("validate", ("-m", "agentweaver", "validate", "--root", ".")),
    ("unittest", ("-m", "unittest", "discover", "-s", "tests", "-v")),
    ("ruff", ("-m", "ruff", "check", ".")),
    ("mypy", ("-m", "mypy", "src/agentweaver")),
    ("build", ("-m", "build", "--no-isolation")),
    ("distribution", ("scripts/check-distribution.py",)),
)
INPUT_PATTERNS = (
    "AGENTS.md", "README.md", "CONTRIBUTING.md", "SECURITY.md", "LICENSE",
    "pyproject.toml", "MANIFEST.in", "build_helpers.py", ".gitignore", ".gitattributes",
    ".gitlab-ci.yml", ".github/workflows/*.yml", "docs/*.md",
    "src/agentweaver/*.py", "src/agentweaver/py.typed", "catalogs/*.json", "profiles/*.json",
    ".agents/skills/*/SKILL.md", "tests/*.py", "tests/fixtures/adapters/*.json",
    "scripts/*.py", "scripts/install.sh", "scripts/install.ps1",
    "benchmarks/README.md", "benchmarks/fixtures/*.jsonl",
)
LIMITATIONS = (
    "This score measures the six documented offline release gates on the recorded host only.",
    "Native vendor installation and configuration consumption remain pending separate manual evidence.",
    "Other OS, architecture and Python combinations require their own recorded runs; CI configuration is not run evidence.",
    "Synthetic benchmarks do not establish measured token or cost savings.",
)


def input_hashes(root):
    paths = {path for pattern in INPUT_PATTERNS for path in root.glob(pattern)}
    result = {}
    for path in sorted(paths):
        # Do not follow linked or junction-backed inputs when describing the tested source.
        for component in (path, *path.relative_to(root).parents):
            candidate = component if component.is_absolute() else root / component
            state = candidate.lstat()
            if candidate.is_symlink() or getattr(state, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked readiness input")
        if not path.is_file():
            raise ValueError("Readiness input must be a regular file")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def tool_versions(root):
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    tools = []
    for requirement in project["project"]["optional-dependencies"]["dev"]:
        name, separator, expected = requirement.partition("==")
        if not separator or not name or not expected:
            raise ValueError("Development tools must be pinned")
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        tools.append({"name": name, "required": expected, "installed": installed,
                      "status": "passed" if installed == expected else "unavailable"})
    if not tools:
        raise ValueError("No development tools declared")
    return tools


def run_gate(root, name, arguments, timeout):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PIP_NO_INDEX"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    environment["NO_COLOR"] = "1"
    started = time.monotonic()
    counts = {}
    try:
        # Logs can contain host paths or fixture values; retain only status in the shareable report.
        completed = subprocess.run(
            [sys.executable, *arguments], cwd=root, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE if name == "unittest" else subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
        exit_code = completed.returncode
        status = "passed" if exit_code == 0 else "failed"
        if name == "unittest":
            summary = completed.stderr or ""
            total = re.search(r"^Ran (\d+) tests? in ", summary, re.MULTILINE)
            skipped = re.search(r"skipped=(\d+)", summary)
            counts = {"tests_run": int(total.group(1)) if total else None,
                      "tests_skipped": int(skipped.group(1)) if skipped else 0}
            if not counts["tests_run"] and status == "passed":
                status = "error"
    except subprocess.TimeoutExpired:
        status, exit_code = "timeout", None
    except OSError:
        status, exit_code = "error", None
    return {"name": name, "command": ["python", *arguments], "status": status,
            "exit_code": exit_code, "duration_seconds": round(time.monotonic() - started, 3), **counts}


def build_report(root, timeout):
    before = input_hashes(root)
    tools = tool_versions(root)
    prerequisites_passed = all(tool["status"] == "passed" for tool in tools)
    checks = [run_gate(root, name, arguments, timeout) for name, arguments in GATES] if prerequisites_passed else [
        {"name": name, "command": ["python", *arguments], "status": "not_run", "exit_code": None}
        for name, arguments in GATES
    ]
    unchanged = before == input_hashes(root)
    passed = sum(check["status"] == "passed" for check in checks)
    score = (100 * passed // len(GATES)) if prerequisites_passed and unchanged else None
    return {
        "schema_version": 1,
        "scope": "offline-release-readiness",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": {"os": platform.system(), "architecture": platform.machine(),
                 "python": platform.python_version()},
        "score": score, "maximum_score": 100,
        "ready": score == 100,
        "passed_gates": passed, "required_gates": len(GATES),
        "inputs_unchanged": unchanged, "input_sha256": before,
        "development_tools": tools, "checks": checks,
        "limitations": list(LIMITATIONS),
    }


def positive_timeout(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Timeout must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--timeout", type=positive_timeout, default=1200, help="Per-gate timeout in seconds")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.root.resolve(), args.timeout)
    except (OSError, ValueError, KeyError, TypeError):
        print("ERROR: Cannot assess readiness inputs or development tool declarations", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
