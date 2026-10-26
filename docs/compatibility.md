# Compatibility

The registry in `catalogs/integrations.json` is authoritative. This release describes **19 agent integrations** across **21 agent surfaces**, plus one OpenRouter provider gateway (`kind: provider`, not an agent). `tests/test_documentation.py` reads the registry at test time and fails when a product, surface, MCP outcome, provider outcome, or count drifts from this page.

Every row below is a **staging** contract: the adapter writes a reviewable artifact under `<agent>/`. No row means the artifact was installed, imported, trusted, or verified with a vendor client. Each registry capability records `sources`, `checked_on`, `fixture_tests`, and `runtime_checks`, and the state of each capability is one of `available`, `manual`, `unsupported`, or `unknown`. **Fixture tests and runtime checks are independent.** Fixture tests prove deterministic output from registry data. Runtime checks require a real product, surface, version, OS variant, and observed result. All rows are dated 2026-10-22 and all are **runtime pending**; no installed-version or native-device verification is recorded.

## Vocabulary

- **Native staged**: the adapter writes a project-scope MCP file at the documented path (`<agent>/<native path>`).
- **Fragment export; manual import**: the adapter writes a user-scope fragment under `<agent>/fragments/`. The operator must merge it into the vendor's user settings by hand. Weaver never writes user-scope settings.
- **Unsupported**: the registry records no documented interface for that capability.
- **Unknown**: the capability is not resolved. This is not a claim of support.
- **bundle gap**: neither instructions nor skills are resolved for the surface, so the instruction bundle command produces nothing for it. This is expected for provider-only surfaces.
- **Registry provider state**: the `provider` capability state recorded in the registry (`available`, `manual`, or `unknown`). OpenRouter handoffs are described in the provider section and are not implied by this column.

## Product and surface table

| Product | Surface | MCP export | Instructions / skills | Scope and documented path | Registry provider state | Evidence |
|---|---|---|---|---|---|---|
| `abacus` | `desktop` | Native staged (`.abacusai/mcp.json`) | `agents-md` root `AGENTS.md` only; skills `manual` (read files, no native skill directory) | project; `.abacusai/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `aider` | `cli` | Unsupported | `read-files`; skills `unsupported` | none; `CONVENTIONS.md` and explicit read files only | `manual` | checked 2026-10-22; runtime pending |
| `amp` | `cli` | Native staged (`.amp/settings.json`) | `agents-md`; skills `available` | project; `.amp/settings.json` | `manual` | checked 2026-10-22; runtime pending |
| `antigravity` | `ide` | Native staged (`.agents/mcp_config.json`, `serverUrl`) | `rules`; skills `available` | project; `.agents/mcp_config.json` | `unknown` | checked 2026-10-22; runtime pending |
| `claude-code` | `cli` | Native staged (`.mcp.json`) | `agents-md`; skills `available` | project; `.mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `cline` | `ide` | Fragment export; manual import (`cline/fragments/cline_mcp_settings.json`) | `rules`; skills `unknown` | user; Cline extension MCP settings | `manual` | checked 2026-10-22; runtime pending |
| `codex` | `cli` | Native staged (`.codex/config.toml`) | `agents-md`; skills `available` | project; `.codex/config.toml`; trusted project only | `available` | checked 2026-10-22; runtime pending |
| `copilot` | `vscode` | Native staged (`.vscode/mcp.json`) | `instructions`; skills `available` | project; `.vscode/mcp.json`; VS Code surface only | `manual` | checked 2026-10-22; runtime pending |
| `cursor` | `ide` | Native staged (`.cursor/mcp.json`) | `rules`; skills `unknown` | project; `.cursor/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `devin` | `desktop-local` | Native staged (`.devin/mcp_config.json`) | `rules`; skills `available` | project; `.devin/mcp_config.json` | `unknown` | checked 2026-10-22; runtime pending |
| `goose` | `cli` | Fragment export; manual import (`goose/fragments/config.yaml`) | `hints`; skills `available` | user; Goose `config.yaml` | `manual` | checked 2026-10-22; runtime pending |
| `junie` | `cli` | Native staged (`.junie/mcp/mcp.json`) | `agents-md`; skills `available` | project; `.junie/mcp/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `kilo` | `cli` | Native staged (`.kilo/kilo.json`) | `agents-md`; skills `available` | project; `.kilo/kilo.json` | `manual` | checked 2026-10-22; runtime pending |
| `kilo` | `ide` | Native staged (`.kilo/kilo.json`) | `agents-md`; skills `available` | project; `.kilo/kilo.json`; same documented schema as the CLI surface | `manual` | checked 2026-10-22; runtime pending |
| `kiro` | `ide` | Native staged (`.kiro/settings/mcp.json`) | `steering`; skills `available` | project; `.kiro/settings/mcp.json` | `unknown` | checked 2026-10-22; runtime pending |
| `opencode` | `cli` | Native staged (`opencode.json`) | `agents-md`; skills `available` | project; `opencode.json` | `manual` | checked 2026-10-22; runtime pending |
| `openhands` | `cli` | Fragment export; manual import (`openhands/fragments/mcp.json`) | `agents-md`; skills `available` | user; `~/.openhands/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `pi` | `cli` | Native staged (`.pi/mcp.json`) | `agents-md`; skills `available` | project; `.pi/mcp.json`; project trust required | `manual` | checked 2026-10-22; runtime pending |
| `qoder` | `cli` | Native staged (`.qoder/settings.json`) | `rules`; skills `available` | project; `.qoder/settings.json`; project approval required | `unknown` | checked 2026-10-22; runtime pending |
| `qoder` | `ide` | Unknown; no MCP exporter (provider-only surface) | bundle gap (instructions and skills unknown) | none; provider-only surface, configured in IDE Settings > Models | `manual` | checked 2026-10-22; runtime pending |
| `zed` | `ide` | Fragment export; manual import (`zed/fragments/settings.json`) | `rules`; skills `available` | user; Zed user `settings.json` | `manual` | checked 2026-10-22; runtime pending |

Only Qoder IDE is a provider-only surface: it has an OpenRouter setup handoff but no MCP export and no instruction or skill bundle. Kilo's two surfaces share one documented project file, and each surface is listed separately because each has its own evidence record.

## Operating-system workflows

| OS family | Contract in this release | Evidence |
|---|---|---|
| `windows` | Primary CI target for the core CLI on Python 3.11+ (GitHub matrix) | CI configuration only; runtime pending |
| `macos` | Primary CI target for the core CLI on Python 3.11+ (GitHub matrix) | CI configuration only; runtime pending |
| `linux` | Primary CI target for the core CLI on Python 3.11+ (GitHub and GitLab matrices) | CI configuration only; runtime pending |
| `bsd` | Conditional CLI target. Variants freebsd, openbsd, netbsd, and dragonflybsd are named separately; Python 3.11+ on a real host is pending. Evidence never crosses variants | pending |
| `solaris` | Conditional CLI target. Variants solaris and illumos are named separately; Python 3.11+ on a real host is pending | pending |
| `android` | Conditional Termux or compatible Python core workflow, plus a remote handoff. Not a general native CLI or phone-local agent claim | pending |
| `ios` | Remote and artifact handoff only, through a separate supported host. No on-device CLI or native mobile application is provided | pending |

Only Windows, macOS, and Linux are primary CI targets. A cross-OS unit simulation is detector coverage, not runtime evidence.

## OpenRouter provider handoffs

`provider` requires `--provider openrouter`, an explicit `--agent`, and `--model`. It uses the declared default surface unless `--surface` is supplied. No model is selected automatically. Each successful handoff writes `<agent>/provider/provider-setup.md`: setup steps, the model convention, endpoint, documented environment variable **names**, restrictions, and sources. Codex additionally writes a native TOML fragment at `codex/provider/config.toml` that names `env_key = "OPENROUTER_API_KEY"` and contains no credential.

Provider setup never reads credential environment-variable values or credential storage. Generated text names variables only. OS detection may consult platform indicators such as WSL and Termux markers. API-key values are never accepted as arguments, and no provider secrets configuration is guessed. Credential storage guarantees are vendor-specific; local config/authentication files are not claimed to be OS-keychain-backed.

| Agent | Surface | Result | Artifacts |
|---|---|---|---|
| `abacus` | `desktop` | Handoff | `abacus/provider/provider-setup.md` |
| `aider` | `cli` | Handoff | `aider/provider/provider-setup.md` |
| `amp` | `cli` | Handoff | `amp/provider/provider-setup.md` |
| `claude-code` | `cli` | Handoff | `claude-code/provider/provider-setup.md` |
| `cline` | `ide` | Handoff | `cline/provider/provider-setup.md` |
| `codex` | `cli` | Handoff | `codex/provider/provider-setup.md`, `codex/provider/config.toml` |
| `copilot` | `vscode` | Handoff | `copilot/provider/provider-setup.md` |
| `cursor` | `ide` | Handoff | `cursor/provider/provider-setup.md` |
| `goose` | `cli` | Handoff | `goose/provider/provider-setup.md` |
| `junie` | `cli` | Handoff | `junie/provider/provider-setup.md` |
| `kilo` | `cli` | Handoff | `kilo/provider/provider-setup.md` |
| `kilo` | `ide` | Handoff | `kilo/provider/provider-setup.md` |
| `opencode` | `cli` | Handoff | `opencode/provider/provider-setup.md` |
| `openhands` | `cli` | Handoff | `openhands/provider/provider-setup.md` |
| `pi` | `cli` | Handoff | `pi/provider/provider-setup.md` |
| `qoder` | `ide` | Handoff (manual setup in the IDE) | `qoder/provider/provider-setup.md` |
| `zed` | `ide` | Handoff | `zed/provider/provider-setup.md` |
| `antigravity` | `ide` | Documented gap | none |
| `devin` | `desktop-local` | Documented gap | none |
| `kiro` | `ide` | Documented gap | none |
| `qoder` | `cli` | Documented gap | none |

Documented gaps: Kiro, Antigravity, and Devin have no documented OpenRouter agent provider setup. Qoder's documented OpenRouter route is the IDE settings surface only; the CLI route is a documented gap and is not inferred from the IDE.

## Instruction and skill bundles

- Instruction files go to `<agent>/<documented instruction path>` only where the registry records an available instruction mechanism.
- Abacus uses the documented root `AGENTS.md` only. There is no native skill directory for Abacus, so its selected skills are manual read files.
- A surface without a documented project instruction target receives `<agent>/handoff/INSTRUCTIONS.md` with vendor-sourced manual steps. No native path is guessed.
- For manual handoffs and for Aider, selected skills are staged as `<agent>/read/<name>.md` and linked from the instruction file. These are portable read files, not native discovery.
- Where the registry records an available native skill path, selected skills are copied to `<agent>/<skills path>/<name>/SKILL.md` with canonical LF bytes. CRLF checkout line endings are normalized; skill content is otherwise unchanged.
- Codex output above the documented 32 KiB limit and Abacus output above 40,000 characters are refused. Content is never truncated.
- Profile guidance is advisory and does not enforce tool use.

## CLI and migration

The CLI provides `inventory`, `render`, `bundle`, `provider`, `doctor`, `report`, and `compare`.

- `render --platform kilo|abacus` is a deprecated alias. It is mutually exclusive with `--agent`, and other integrations require `--agent`.
- Kilo output no longer emits the unsupported `enabled: true` field.
- `--dry-run` prints a JSON envelope with `schema_version: 1` and an `artifacts` list. Each item contains the relative `path`, the `kind`, and UTF-8 `content`. Dry-run validates artifact contents, portable paths and conflicts without writing. Destination ownership and permissions are checked only by an actual write.
- `bundle --skill` is explicit. Skills are never included by default.
- Profile names must be safe identifiers. Custom profiles are loaded only from an explicit `--root`.
- Normal wheel and sdist installs read public catalogs, profiles, and skills through `importlib.resources`. There is no checkout or CWD fallback. Editable and source development must pass `--root .` and set `PYTHONPATH=src`.

## Staging ownership, overwrite, and limits

Outputs from `render`, `bundle`, and `provider` are staged vendor configuration and instruction artifacts. Their `--force` option may replace only artifacts recorded as owned in `.agentweaver-manifest.json` whose current SHA-256 still matches the recorded hash. Manually edited or unmanaged files are refused. The manifest records deterministic relative paths, hashes, kinds, and generator and schema identifiers. It excludes secrets and absolute user paths. Entries for untouched outputs are preserved.

For those staged artifacts, symbolic links, junctions and other reparse points, traversal, absolute artifact paths, and conflicting paths are refused. Hash matching is change detection, **not a signature** and not audit evidence of tampering. The writer is not a bundle-wide atomic transaction and is not a cross-process race guarantee.

Benchmark `report` and `compare` output is an ordinary report document, outside the staging manifest. An explicit `--output` writes or replaces that report using normal filesystem semantics. See [benchmark-methodology.md](benchmark-methodology.md) for this separate output contract.

## Security defaults

MCP is default-off. The `enterprise-secure` profile generates zero MCP selections. This applies only to generated artifacts: ambient and global vendor configuration is not inspected, and pre-existing vendor servers are not disabled. Profile retrieval instructions are advisory and are not tool enforcement, a sandbox, a permission grant, or a compliance control.

No vendor entitlement, SSO, or enterprise compliance certification is provided or implied. Native mobile applications, an autonomous agent execution harness, automatic agent installation, live configuration writes, credential discovery, and production-write automation are out of scope.

## Verification status

Native vendor installation and configuration-consumption checks are pending and must be recorded separately with product, surface, version, OS variant, architecture, and observed result. `scripts/verify-platform.py` records local offline checks only and is opt-in; it requires an explicit `--output` path.
