# Agent Efficiency Weaver

Portable, provider-independent **Agent Skills**, **MCP configuration adapters**, and reproducible **token/cost/quality benchmarks** for AI coding agents.

**Status:** v0.1 foundation. The data-driven registry covers **31 products: 25 agents, 4 providers, and 2 hybrids**. A hybrid has distinct application/MCP-host and model-provider surfaces. Adapters stage reviewable artifacts; they do not install, import, launch, or verify vendor clients. This repository does **not** claim verified token savings, automatic proprietary-agent telemetry, or installed-version compatibility.

## What is included

- A registry of agent products and surfaces in `catalogs/integrations.json`, with documentation URLs, checked dates, version constraints, and independent fixture/runtime evidence fields.
- Seven OS families in `catalogs/operating-systems.json`, with primary CI targets limited to Windows, macOS, and Linux.
- Native staged MCP artifacts only where a documented schema is implemented, including remote HTTPS and product-specific stdio command/argument formats. User-scope fragments are staged for manual import; remote-only API request fragments reject stdio. Unknown, unsupported, and unresolved surfaces remain visibly labeled in [docs/compatibility.md](docs/compatibility.md).
- Separate default-off Perplexity API and Computer MCP entries. Computer uses account OAuth and credits; adding a staged URL does not authenticate, connect, or start a task.
- Portable instructions and six canonical Skills under `.agents/skills/`, copied with canonical LF line endings where a native path is documented; CRLF checkout line endings are normalized without rewriting skill content. Cline, Cursor, and Qoder IDE use their documented native Skills paths. Perplexity app and LM Studio Bionic receive standard `SKILL.md` files under `<agent>/imports/skills/` for manual vendor import. Other manual read-file routes and Abacus use `<agent>/read/<name>.md`; Abacus has only the root `AGENTS.md` and no native skill directory.
- An OpenRouter provider handoff per supported surface: `<agent>/provider/provider-setup.md`, plus a credential-free native TOML fragment for Codex. It requires an explicit model and never accepts API-key values.
- Requested-scope tracking in `catalogs/requested-coverage.json`: 23 requested groups map to 34 distinct registry surfaces. The report shows unknown capabilities, missing documentation, and pending runtime evidence separately. Mapping a requested name does not establish support.
- Offline model/provider inventory and caller-supplied model snapshots, with explicit model selection and evidence-dependent reasoning controls. Model catalogs change; no exhaustive static model list or account access is promised.
- Custom-agent definition inventory and staged native definitions or manual handoffs, with separate model, reasoning, and permission behavior for each documented route.
- Staged artifact ownership through `.agentweaver-manifest.json`, safe writes, and normalized benchmark reporting.

## Install and run

Requires Python 3.11+ and no runtime dependencies.

```bash
python -m pip install .
agentweaver validate
agentweaver inventory --format text
agentweaver coverage --format json
agentweaver models --provider openai --format json
agentweaver agents inventory --agent claude-code --format json
agentweaver render --agent kilo --profile efficient --output-dir generated
agentweaver render --agent codex --profile efficient --enable context7 --output-dir generated
agentweaver bundle --agent aider --profile efficient --skill token-efficient-retrieval --output-dir generated
agentweaver provider --provider openrouter --agent aider --model vendor/chosen-model --output-dir generated
agentweaver model --agent qwen-code --provider openai --model gpt-6.1-sol --protocol openai-responses --reasoning-effort high --dry-run
agentweaver doctor --agent kilo --os windows --format text
```

`--platform kilo|abacus` remains a legacy spelling and is mutually exclusive with `--agent`. Generated files are staging artifacts. Review every artifact and merge it manually; no MCP server is enabled by default.

Aliases such as `OpenAIcodex`, `kiloAI`, `FactoryDroid`, and `LMStudio` resolve through the requested-scope catalog. `VSCodeagents` is a group of named extension surfaces; inspect it with `coverage` and select a concrete product before rendering. JetBrains Air and Junie are separate products; Air desktop and Air in IDEs are distinct `desktop` and `ide` surfaces. ChatGPT Work local/cloud, Perplexity app/API, and LM Studio desktop/Bionic/API are separate surfaces.

Every capability record in the requested scope now has reviewed documentation sources. The report still contains unknown and unsupported states, manual routes, and pending vendor runtime checks. The per-surface OS table in [compatibility.md](docs/compatibility.md#requested-surface-os-workflows) records documented routes and constraints without claiming installed or native-device verification.

`models --provider ID --input snapshot.json` reads a model response captured separately by the operator. It performs no discovery request. `model --agent PRODUCT --provider PROVIDER --model ID` stages model setup; optional reasoning controls are validated for the selected provider, protocol, and documented model or snapshot capabilities. Catalog visibility is not successful inference, installed-version compatibility, or account access. Missing availability, token, or cost data stays unknown.

A provider-reported context length of zero stays visible in inventory as the reported value and produces an unknown usable context size. It does not configure a zero-length context window. Explicit `--context-size` values must be positive; Kimi native model configuration still requires an established positive limit.

For a separately captured OpenRouter inventory across model types, use its documented `output_modalities=all` filter; the endpoint otherwise defaults to text output. Imported conditional prices remain conditional, unresolved prices remain unknown, and modality metadata helps distinguish task-specific models from language-agent model choices. See the [model snapshot contract](docs/compatibility.md#models-and-reasoning).

`models --agent PRODUCT --surface SURFACE` inspects that host's documented model chooser and reasoning controls. This host inventory is separate from importing a provider snapshot with `--input`.

The host-control inventory covers all 43 registered surfaces and preserves unknown availability. JetBrains Air desktop's shared reasoning override remains unknown. Air in IDEs separately documents agent-dependent model and effort controls through manual presets and session settings. OpenHands CLI has model-dependent effort and thinking-budget controls through its pinned SDK configuration; those controls remain separate from custom-agent fields. A documented effort, budget, toggle, variant, or mode remains specific to the selected host and model. The inventory does not enable additional native exporters or prove access to a listed model.

For `--agent jetbrains-air --surface ide`, bundles stage advisory instructions and `<agent>/read/<name>.md` context files. Apply instructions through a preset's Advanced > Injected instructions; Add context > Skills or `$` selects Skills already exposed by the host, not these staged files. Manual custom-agent output describes a UI preset, without inventing an agent file, Skill import loader, OpenRouter configuration or IDE OS matrix. The Air desktop route keeps its separate contract.

`agents inventory` reports custom-agent definition support separately from project instructions and Skills. `agent --agent PRODUCT --name NAME --description TEXT --instructions-file instructions.md` stages a documented definition or a manual handoff. Optional model and reasoning fields follow that definition format's restrictions. No definition is installed or launched, and no permissions are granted by generating it.

The OpenHands route is scoped to audited CLI 1.16.0 with SDK 1.28.1: it stages `.agents/agents/<name>.md` with `model: inherit` by default and `tools: []`. Use `agent --agent openhands ... --llm-profile NAME` to reference an existing named local LLM profile instead. Weaver records that reference without reading the profile, checking its existence, or creating it. A profile reference cannot be combined with raw `--model`, provider, catalog, or effort overrides; profile availability, compatibility, and inference remain unknown. Its user MCP fragment supports remote URLs and structured stdio for manual import; model/provider setup remains a CLI Settings handoff. See the [OpenHands boundaries](docs/compatibility.md#custom-agent-definitions).

Normal wheel and sdist installs read public assets with `importlib.resources`; there is no checkout or CWD fallback. For editable or other source development, pass `--root .` and set `PYTHONPATH=src`:

```powershell
python -m pip install -e ".[dev]"
$env:PYTHONPATH = "src"
python -m agentweaver validate --root .
```

On POSIX shells, use `PYTHONPATH=src python -m agentweaver validate --root .` for a source run.

## Safety contract

- MCP is default-off. Stdio catalogs use one executable plus a structured argument array; Weaver validates and stages it without launching the executable, invoking a shell, installing a package, or expanding environment variables. The `enterprise-secure` profile generates zero MCP selections for generated artifacts. Ambient and global vendor configuration is not inspected, and profile retrieval advice is advisory, not tool enforcement.
- For staged vendor configuration and instruction artifacts, `--force` replaces only owned artifacts whose current SHA-256 matches the manifest record. Links, junctions, traversal, absolute artifact paths, secrets in manifests, and unmanaged files are refused. Hashes are not signatures and the writer is not a bundle-wide atomic transaction or a cross-process race guarantee. Benchmark report output has the separate ordinary-file contract in [docs/benchmark-methodology.md](docs/benchmark-methodology.md).
- `--dry-run` prints a `schema_version: 1` envelope containing an `artifacts` list with each `path`, `kind`, and `content`. It writes nothing.
- Provider commands require an explicit `--model`, use documented environment-variable references where available, and never accept API-key values as arguments. Provider setup never reads credential environment-variable values; generated setup text names variables only. OS detection may consult platform indicators.
- No network requests, paid calls, agent execution, credential discovery, live configuration writes, or production changes occur during ordinary validation, coverage, model inventory/setup, rendering, bundling, diagnostics, or tests.

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

The readiness guide preserves separate immutable CI checkpoints for the earlier 20-product release and the initial 31-product expansion at `b2ef30f`. Each describes its own source commit; later changes require their own successful gates and CI run.

`scripts/verify-platform.py` is opt-in, requires an explicit `--output` path, and records local checks only. It records the selected source tree's version and rejects linked report destinations. Native vendor installation and configuration-consumption checks remain a separate manual check.

## License

0BSD for original repository content. Third-party names, services, packages and dependencies retain their respective licenses.

See [AGENTS.md](AGENTS.md), [SECURITY.md](SECURITY.md), and [CONTRIBUTING.md](CONTRIBUTING.md).
