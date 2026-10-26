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

## Platform verification

`scripts/verify-platform.py` is opt-in and requires an explicit `--output` path:

```bash
python scripts/verify-platform.py --output evidence.json
```

It records local offline checks and host metadata only. Native agent installation and configuration-consumption evidence is a separate manual check and must record product, surface, version, OS variant, architecture, artifact hashes, exact action, and observed result. Missing access is reported as pending evidence, not as a pass.

The package version comes from the selected `--root` tree's `pyproject.toml`; an unrelated installed distribution is never substituted. Platform evidence report output refuses links, reparse points, traversal, and nonregular destinations. Its explicit `--force` replaces an ordinary existing evidence report; this is separate from the owned-hash rule for staged vendor artifacts. Platform evidence includes subprocess output and local paths, so keep it outside commits and review it before sharing.

## Staged artifacts

Do not overwrite user configuration. Generated vendor configuration and instruction files are staging artifacts with a manifest of relative paths and hashes. Their `--force` option may replace only owned artifacts whose current hash matches. Do not weaken link, junction, traversal, secret, or absolute-artifact-path checks to make a test pass. Benchmark reports have a separate ordinary-file output contract in [docs/benchmark-methodology.md](docs/benchmark-methodology.md).
