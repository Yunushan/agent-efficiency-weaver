# Contributing

Changes should be small, testable, documented, and licensed under 0BSD when originally contributed. Keep network access, credentials, agent execution, and paid LLM calls out of default tests and CI. Include before/after evidence for any performance claim and disclose whether evidence is synthetic or measured.

## Development setup

Install the declared development dependencies explicitly before running the offline gates:

```bash
python -m pip install ".[dev]"
```

The development extra pins:

```text
ruff==0.16.2
mypy==2.3.1
build==1.5.0
setuptools==84.0.0
wheel==0.47.0
PyYAML==6.0.3
```

Development tools are not runtime dependencies. The quality gates are offline once those tools are installed.

GitHub CI runs the full Windows/macOS/Linux and Python 3.11–3.13 matrix for pull requests, pushes to `main`, and manual dispatches. Feature-branch pushes rely on their pull request run, avoiding duplicate matrices for the same update.

## Required gates

Run every gate before opening a pull request:

```bash
python -m agentweaver validate
python -m unittest discover -s tests -v
python -m ruff check .
python -m mypy src/agentweaver
python -m build --no-isolation
python scripts/check-distribution.py
```

`python -m mypy src/agentweaver` is a strict pipeline: `pyproject.toml` sets `disallow_untyped_defs = true` with `check_untyped_defs`, `warn_unused_ignores`, and `no_implicit_optional`. New or changed functions in `src/agentweaver` must carry complete type annotations. The distribution check builds and installs outside the checkout and must not need network access or credentials.

Distribution verification requires runtime modules, the `py.typed` marker, and public assets to match the selected checkout in both archives and the rebuilt wheel. Source build hooks and project configuration must match before the verifier executes the sdist build. A stale archive cannot substitute for testing the current code.

`python scripts/check-readiness.py` runs these six gates in order, using the selected source tree for validation and tests, and emits a shareable JSON summary. The installed development tools must match the pins above. A missing or mismatched tool leaves the score unknown and the gates unrun. Failed or timed-out gates prevent a full score. Input changes during the run invalidate the score. The output contains relative input hashes, normalized commands, host/Python metadata, and exit statuses; it omits subprocess logs and absolute host paths. Rerun the listed gate directly to inspect a failure. The rubric and its scope are documented in [docs/readiness.md](docs/readiness.md).

## Documentation and evidence

Documentation changes must stay aligned with the registries. Tests read the actual catalogs and fail when a product, surface, OS family, evidence state, or provider gap is missing or misrepresented. Do not document a native exporter for a manual surface, do not describe fixture tests as installed-version verification, and do not add vendor entitlement, SSO, compliance certification, native mobile, autonomous execution, or numerical performance claims without reproducible evidence.

The current registry contains 31 products: 25 agents, 4 providers, and 2 hybrids. Product and surface IDs must come from `catalogs/integrations.json`. Keep requested labels and aliases in `catalogs/requested-coverage.json`; every target must resolve and a group alias must not silently select an exporter. A mapped request is not evidence that all its capabilities work. Hybrid provider interfaces and application/MCP-host surfaces are separate contracts.

Model/provider routes belong in `catalogs/model-providers.json`. Add documented protocols, snapshot formats, and model-specific reasoning evidence rather than a supposedly exhaustive list of evolving model IDs. Treat a caller-supplied listing as catalog visibility, not successful inference or account access. Keep missing availability, token, and cost data unknown. Never commit captured private account catalogs, real credentials, or provenance containing private paths. Synthetic model snapshots must be clearly identified in tests.

Preserve a provider-reported zero context length in inventory while leaving the derived usable context unknown. Explicit `--context-size` must be a positive integer. Do not turn a sentinel into a configured zero window or bypass Kimi's positive context requirement; keep its output a manual template when the usable limit is unknown.

Maintain one `catalogs/model-hosts.json` row per registered product/surface. Host picker, field, session-command, and UI evidence must stay specific to that surface. A manual documented control does not activate a native exporter; null allowed values require selected-model discovery. Do not transfer a CLI-only effort command to an IDE or treat an API model response as the app's account catalog. Host availability remains unknown until separately recorded evidence establishes more.

Keep Air desktop and Air in IDEs separate. The IDE plugin documents manual UI presets, injected instructions, host Skills selection and agent-dependent model/effort controls; its selected portable Skills are ordinary read-file context. No native preset file, Skill import loader, arbitrary OpenRouter endpoint, or complete IDE OS matrix is established. Documented agent-dependent MCP use does not activate an MCP file exporter.

Custom-agent definitions are separate from portable Skills and project instructions. Test their native/manual/unknown/unsupported outcomes, actual model and reasoning fields, and permission limitations. New Python behavior requires unit tests. New packaged catalogs must be added to both runtime/build asset allowlists, `MANIFEST.in`, resource tests, installed-package smoke checks as appropriate, and `validate`. Run every portable Skill validator through the documented gates.

When an application consumes an SDK format, record the pinned application-to-SDK loader chain. OpenHands CLI 1.16.0/SDK 1.28.1 is one such scoped contract: its exported file inherits the parent model by default and declares no external tools, while non-inherit vendor file-agent model values name saved local LLM profiles. Manual host reasoning controls and raw provider model IDs are separate contracts. Preserve exact source and fixture references when updating these routes; source inspection is not an installed-client check.

MCP catalog entries may be remote HTTPS or stdio. Stdio requires a bounded executable string and a separate bounded argument array; do not add raw shell-command, credential, environment, or permission fields. Keep default selection off and require explicit profile permission and `--enable`. Product-specific stdio documentation and fixture evidence are separate from remote URL evidence. Tests must never launch the staged executable, install a package, or expand runtime variables. Perplexity Agent API and LM Studio ephemeral MCP request formats remain remote HTTPS only.

Keep the Perplexity API and Computer MCP services separate. Computer uses account OAuth and credits, can expose connected-service write actions, and discovers model/effort options per authenticated account through `models_list`. Never add authentication material, automatic OAuth or task execution to staged configuration; shipped profiles must not implicitly allow either service.

GitHub's default and all-toolset MCP URLs have distinct read-only variants. Preserve the exact documented URL for each route, keep all of them outside shipped allowlists and default-off, and disclose that non-read-only routes can expose write tools. The read-only filter applies only to that GitHub server connection; profile advice and other servers are separate boundaries.

Keep OpenAI's documentation-only MCP endpoint separate from the historical Codex MCP server. The current removal guidance does not make the experimental JSON-RPC app server an MCP replacement. Do not install, configure or start either service as part of validation.

Computer `models_list` snapshots are caller-supplied and account-scoped. Keep offline inspection and mode/model/effort validation separate from provider API discovery; missing effort lists stay unknown. Use synthetic fixtures, reject credential-bearing input and do not commit real account snapshots.

Kimi web model-list snapshots are caller-supplied configured-alias observations, not provider catalogs. Keep aliases, model-specific effort lists, optional replacement metadata and unknown states distinct; use synthetic fixtures and never collect provider details or tokens. The documented provider-detail endpoint can reveal a stored API key.

The remote CI runs in [docs/readiness.md](docs/readiness.md) identify separate immutable checkpoints for the earlier 20-product release and the initial 31-product expansion. Neither substitutes for validating later model, alias, request-fragment, custom-agent, or documentation-evidence changes.

Preserve documented unknown states when first-party sources establish a boundary without establishing a schema. OS `available` means a documented route on the constrained platform, while `manual` can mean conditional or remote access; neither supplies runtime verification. Keep version, architecture, OS variant and account conditions visible. App-scope MCP fragments must not become guessed persistent settings paths, and manual Skill imports must retain the vendor-required `SKILL.md` filename without being described as native discovery.

## Platform verification

`scripts/verify-platform.py` is opt-in and requires an explicit `--output` path:

```bash
python scripts/verify-platform.py --output evidence.json
```

It records local offline checks and host metadata only. Native agent installation and configuration-consumption evidence is a separate manual check and must record product, surface, version, OS variant, architecture, artifact hashes, exact action, and observed result. Missing access is reported as pending evidence, not as a pass.

The package version comes from the selected `--root` tree's `pyproject.toml`; an unrelated installed distribution is never substituted. Platform evidence report output refuses links, reparse points, traversal, and nonregular destinations. Its explicit `--force` replaces an ordinary existing evidence report; this is separate from the owned-hash rule for staged vendor artifacts. Platform evidence includes subprocess output and local paths, so keep it outside commits and review it before sharing.

## Staged artifacts

Do not overwrite user configuration. Generated vendor configuration and instruction files are staging artifacts with a manifest of relative paths and hashes. Their `--force` option may replace only owned artifacts whose current hash matches. Do not weaken link, junction, traversal, secret, or absolute-artifact-path checks to make a test pass. Benchmark reports have a separate ordinary-file output contract in [docs/benchmark-methodology.md](docs/benchmark-methodology.md).
