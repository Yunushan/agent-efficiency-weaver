# Agent Efficiency Weaver

Portable, provider-independent **Agent Skills**, **MCP configuration adapters**, and reproducible **token/cost/quality benchmarks** for AI coding agents.

**Status:** v0.1 foundation. The data-driven registry covers **19 agent integrations and one OpenRouter provider gateway**. Adapters stage reviewable artifacts; they do not install, import, launch, or verify vendor clients. This repository does **not** claim verified token savings, automatic proprietary-agent telemetry, or installed-version compatibility.

## What is included

- A registry of agent products and surfaces in `catalogs/integrations.json`, with documentation URLs, checked dates, version constraints, and independent fixture/runtime evidence fields.
- Seven OS families in `catalogs/operating-systems.json`, with primary CI targets limited to Windows, macOS, and Linux.
- Native staged MCP artifacts only where a documented schema is implemented. User-scope fragments are staged for manual import. Unknown, unsupported, and unresolved surfaces remain visibly labeled in [docs/compatibility.md](docs/compatibility.md).
- Portable instructions and six canonical Skills under `.agents/skills/`, copied with canonical LF line endings where a native path is documented; CRLF checkout line endings are normalized without rewriting skill content. Manual surfaces and Abacus receive selected skills as `<agent>/read/<name>.md` read files; Abacus uses only the root `AGENTS.md` and has no native skill directory.
- An OpenRouter provider handoff per supported surface: `<agent>/provider/provider-setup.md`, plus a credential-free native TOML fragment for Codex. It requires an explicit model and never accepts API-key values.
- Staged artifact ownership through `.agentweaver-manifest.json`, safe writes, and normalized benchmark reporting.

## Install and run

Requires Python 3.11+ and no runtime dependencies.

```bash
python -m pip install .
agentweaver validate
agentweaver inventory --format text
agentweaver render --agent kilo --profile efficient --output-dir generated
agentweaver render --agent codex --profile efficient --enable context7 --output-dir generated
agentweaver bundle --agent aider --profile efficient --skill token-efficient-retrieval --output-dir generated
agentweaver provider --provider openrouter --agent aider --model vendor/chosen-model --output-dir generated
agentweaver doctor --agent kilo --os windows --format text
```

`--platform kilo|abacus` remains a legacy spelling and is mutually exclusive with `--agent`. Generated files are staging artifacts. Review every artifact and merge it manually; no MCP server is enabled by default.

Normal wheel and sdist installs read public assets with `importlib.resources`; there is no checkout or CWD fallback. For editable or other source development, pass `--root .` and set `PYTHONPATH=src`:

```powershell
python -m pip install -e ".[dev]"
$env:PYTHONPATH = "src"
python -m agentweaver validate --root .
```

On POSIX shells, use `PYTHONPATH=src python -m agentweaver validate --root .` for a source run.

## Safety contract

- MCP is default-off. The `enterprise-secure` profile generates zero MCP selections for generated artifacts. Ambient and global vendor configuration is not inspected, and profile retrieval advice is advisory, not tool enforcement.
- For staged vendor configuration and instruction artifacts, `--force` replaces only owned artifacts whose current SHA-256 matches the manifest record. Links, junctions, traversal, absolute artifact paths, secrets in manifests, and unmanaged files are refused. Hashes are not signatures and the writer is not a bundle-wide atomic transaction or a cross-process race guarantee. Benchmark report output has the separate ordinary-file contract in [docs/benchmark-methodology.md](docs/benchmark-methodology.md).
- `--dry-run` prints a `schema_version: 1` envelope containing an `artifacts` list with each `path`, `kind`, and `content`. It writes nothing.
- Provider commands require an explicit `--model`, use documented environment-variable references where available, and never accept API-key values as arguments. Provider setup never reads credential environment-variable values; generated setup text names variables only. OS detection may consult platform indicators.
- No network requests, paid calls, agent execution, credential discovery, live configuration writes, or production changes occur during ordinary validation, rendering, bundling, diagnostics, or tests.

## Compatibility and scope

See [docs/compatibility.md](docs/compatibility.md) for the per-product/surface table, the seven-family OS contract, provider gaps, migration notes, and evidence semantics. See [docs/architecture.md](docs/architecture.md) for module boundaries and [docs/roadmap.md](docs/roadmap.md) for release scope.

The project does not provide native mobile applications, an autonomous agent execution harness, vendor entitlement or SSO provisioning, an enterprise compliance certification, or measured numerical token/cost improvements. BSD and Solaris remain conditional pending real-host evidence; Android is conditional Termux or remote handoff; iOS is remote/artifact handoff only.

## Development gates

Development tooling is installed separately and runs offline after dependencies are present:

```bash
python -m pip install ".[dev]"
python -m agentweaver validate
python -m unittest discover -s tests -v
python -m ruff check .
python -m mypy src/agentweaver
python -m build --no-isolation
python scripts/check-distribution.py
```

For a repeatable score, run `python scripts/check-readiness.py`. It emits a JSON report with a score of 100 only when all six offline release gates pass with the pinned development tools and unchanged source inputs. The score applies to the recorded host, Python version, and architecture. See [docs/readiness.md](docs/readiness.md) for the rubric, rerun instructions, and evidence limits.

`scripts/verify-platform.py` is opt-in, requires an explicit `--output` path, and records local checks only. It records the selected source tree's version and rejects linked report destinations. Native vendor installation and configuration-consumption checks remain a separate manual check.

## License

0BSD for original repository content. Third-party names, services, packages and dependencies retain their respective licenses.

See [AGENTS.md](AGENTS.md), [SECURITY.md](SECURITY.md), and [CONTRIBUTING.md](CONTRIBUTING.md).
