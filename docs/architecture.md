# Architecture

The Python core stays dependency-free at runtime and reads public data from `catalogs/`, `profiles/`, and `.agents/skills/`. Installed distributions copy that allowlisted data into the package; `src/agentweaver/resources.py` resolves it with `importlib.resources`. An explicit `--root` overrides bundled resources only for intentional source or custom work, with no checkout or CWD fallback. Editable development uses `--root .` plus `PYTHONPATH=src`.

## Data and evidence model

`catalogs/integrations.json` is the source of truth for 19 agents and one OpenRouter provider gateway, with stable product and surface IDs. Each surface records its MCP dialect, config scope, documented path hint, instruction mode and paths, skills path, OS workflow states, and evidence for `mcp`, `instructions`, `skills`, `provider`, and `os`. Evidence fields `sources`, `checked_on`, `version_constraint`, `fixture_tests`, and `runtime_checks` are independent. A catalog entry, fixture test, or portable Python implementation is not installed-version or native-device evidence.

`catalogs/operating-systems.json` records seven families: Windows, macOS, Linux, BSD, Solaris, Android, and iOS. Windows, macOS, and Linux are primary CI targets. BSD and Solaris require Python 3.11+ and real-host evidence, Android is conditional Termux/compatible Python plus remote handoff, and iOS is remote/artifact handoff only. Host detection preserves WSL, BSD variant, and illumos qualifiers.

## Rendering pipeline

`src/agentweaver/integrations.py` parses registries strictly and exposes immutable specifications. `src/agentweaver/adapters.py` maps only implemented product surfaces to documented dialect fragments, dispatching on the registry dialect ID; project-scope dialects are staged under `<agent>/<native path>` and user-scope fragments under `<agent>/fragments/` for manual import. It does not infer a schema from a similar product name. `src/agentweaver/skills.py` validates the six canonical skills and projects only documented instruction or skill mechanisms, with explicit manual handoffs where native discovery is unavailable. Manual handoffs and Abacus stage selected skills as `<agent>/read/<name>.md` read files that the instruction file links to; Abacus receives only the root `AGENTS.md` and no native skill directory. `src/agentweaver/providers.py` handles OpenRouter separately as a provider gateway and requires explicit model selection. Each supported surface produces `<agent>/provider/provider-setup.md`; Codex also produces `codex/provider/config.toml`, a credential-free native TOML fragment. Unsupported routes are documented gaps and fail closed.

`src/agentweaver/artifacts.py` validates every relative path and content before writing. It refuses links, junctions, traversal, absolute paths, conflicting destinations, and unmanaged overwrites. Manifest entries record relative paths, hashes, and kinds; hashes support change detection, not signatures or audit guarantees. The writer is not a bundle-wide atomic transaction and does not claim cross-process race resistance.

## Diagnostics and boundaries

`src/agentweaver/platforms.py` keeps core-runtime eligibility, adapter availability, vendor-native availability, manual routes, and remote routes separate. `doctor` reports pending evidence and never installs or launches tools. Rendering a target-OS handoff does not imply that files execute on that target.

Generated MCP selections are default-off and staging-only. The `enterprise-secure` profile generates zero MCP selections for staged output; ambient and global vendor configuration is not inspected. Profile retrieval rules are advisory and not tool enforcement. Nothing in the ordinary render, bundle, provider, doctor, report, or compare path performs network access, credential discovery, agent execution, production writes, or paid calls.

Benchmark code remains independent of integration rendering. Missing token or cost data stays unknown, synthetic fixtures stay separate from measured evidence, and no numerical efficiency claim is derived from catalog metadata.

`scripts/check-readiness.py` runs the six required offline release gates with pinned development tools and hashes the relevant public source inputs before and after the run. Its scoped score describes only that local gate run; native vendor checks and other host combinations require separate evidence. `scripts/verify-platform.py` records source-version provenance and a smaller local validation/test run, with native verification explicitly pending. Neither tool upgrades registry runtime evidence automatically.
