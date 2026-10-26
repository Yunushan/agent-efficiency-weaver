# Compatibility

The registry in `catalogs/integrations.json` is authoritative. This release describes **31 products: 25 agent integrations, 4 provider integrations, and 2 hybrid integrations**. There are **32 agent surfaces** and **6 hybrid surfaces**, plus 4 pure-provider surfaces. A hybrid combines explicitly separate application/MCP-host and provider API surfaces; it does not make every capability available on every surface. `tests/test_documentation.py` compares every product/surface row and exporter outcome with the actual registry.

Every output described below is a **staging** contract: the adapter writes a reviewable artifact under `<agent>/`. Catalog presence does not mean an artifact was installed, imported, trusted, or consumed by a vendor client. Each registry capability records `sources`, `checked_on`, `version_constraint`, `fixture_tests`, and `runtime_checks`, and has a state of `available`, `manual`, `unsupported`, or `unknown`. **Fixture tests and runtime checks are independent.** Fixture tests check deterministic output; runtime checks require a real product, surface, version, OS variant, architecture, and observed result. Documentation dates vary by entry. All listed surfaces remain **runtime pending**; no installed-version or native-device verification is recorded.

## Vocabulary

- **Native staged**: the adapter writes a project-scope MCP file at the documented path (`<agent>/<native path>`).
- **Fragment export; manual import**: the adapter writes a user-scope fragment under `<agent>/fragments/`. Merge it into the vendor's settings deliberately. Weaver never writes the live user settings.
- **Request fragment**: an API request body is staged under `<agent>/requests/<surface>.json`. It is neither a repository configuration file nor a sent API request.
- **Not exported**: documentation identifies a manual interface, but this release does not implement a native MCP file exporter for it.
- **Unsupported**: documentation establishes no supported interface for that capability.
- **Unknown**: the capability is unresolved. This is not a claim of support.
- **bundle gap**: instructions and skills are unknown or unsupported, so no instruction bundle is rendered for the surface.
- **Registry provider state**: the surface's documented `provider` capability. An available provider interface does not imply an implemented OpenRouter handoff, a model route, or account access.

## Product and surface table

| Product | Surface | MCP export | Instructions / skills | Scope and documented path | Registry provider state | Evidence |
|---|---|---|---|---|---|---|
| `abacus` | `desktop` | Native staged (`abacus/.abacusai/mcp.json`) | `agents-md` root `AGENTS.md` only; skills `manual` (read files, no native skill directory) | project; `.abacusai/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `aider` | `cli` | Unsupported | `read-files`; instructions `available`; skills `unsupported` | none; no native repository path declared | `manual` | checked 2026-10-22; runtime pending |
| `amp` | `cli` | Native staged (`amp/.amp/settings.json`) | `agents-md`; instructions `available`; skills `available` | project; `.amp/settings.json` | `manual` | checked 2026-10-22; runtime pending |
| `antigravity` | `ide` | Native staged (`antigravity/.agents/mcp_config.json`) | `rules`; instructions `available`; skills `available` | project; `.agents/mcp_config.json` | `unknown` | checked 2026-10-22; runtime pending |
| `chatgpt` | `desktop` | Not exported; manual interface | `manual`; instructions `manual`; skills `manual` | app; no native repository path declared | `unknown` | checked 2026-10-26; runtime pending |
| `chatgpt` | `web` | Not exported; manual interface | `manual`; instructions `manual`; skills `manual` | app; no native repository path declared | `unknown` | checked 2026-10-26; runtime pending |
| `chatgpt` | `work-local` | Not exported; manual interface | `manual`; instructions `manual`; skills `manual` | app; no native repository path declared | `unknown` | checked 2026-10-26; runtime pending |
| `chatgpt` | `work-cloud` | Not exported; manual interface | `manual`; instructions `manual`; skills `manual` | app; no native repository path declared | `unknown` | checked 2026-10-26; runtime pending |
| `claude-code` | `cli` | Native staged (`claude-code/.mcp.json`) | `agents-md`; instructions `available`; skills `available` | project; `.mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `cline` | `ide` | Fragment export; manual import (`cline/fragments/cline_mcp_settings.json`) | `rules`; instructions `available`; skills `unknown` | user; Cline extension MCP settings (user scope); CLI: ~/.cline/data/settings/cline_mcp_settings.json | `manual` | checked 2026-10-22; runtime pending |
| `codex` | `cli` | Native staged (`codex/.codex/config.toml`) | `agents-md`; instructions `available`; skills `available` | project; `.codex/config.toml` | `available` | checked 2026-10-22; runtime pending |
| `codex` | `desktop` | Native staged (`codex/.codex/config.toml`) | `agents-md`; instructions `available`; skills `available` | project; `.codex/config.toml` | `available` | checked 2026-10-26; runtime pending |
| `codex` | `ide` | Native staged (`codex/.codex/config.toml`) | `agents-md`; instructions `available`; skills `available` | project; `.codex/config.toml` | `available` | checked 2026-10-26; runtime pending |
| `copilot` | `vscode` | Native staged (`copilot/.vscode/mcp.json`) | `instructions`; instructions `available`; skills `available` | project; `.vscode/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `cursor` | `ide` | Native staged (`cursor/.cursor/mcp.json`) | `rules`; instructions `available`; skills `unknown` | project; `.cursor/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `devin` | `desktop-local` | Native staged (`devin/.devin/mcp_config.json`) | `rules`; instructions `available`; skills `available` | project; `.devin/mcp_config.json` | `unknown` | checked 2026-10-22; runtime pending |
| `factory` | `cli` | Native staged (`factory/.factory/mcp.json`) | `agents-md`; instructions `available`; skills `available` | project; `.factory/mcp.json` | `manual` | checked 2026-10-26; runtime pending |
| `goose` | `cli` | Fragment export; manual import (`goose/fragments/config.yaml`) | `hints`; instructions `available`; skills `available` | user; ~/.config/goose/config.yaml (Windows: %APPDATA%\Block\goose\config\config.yaml) | `manual` | checked 2026-10-22; runtime pending |
| `hermes` | `cli` | Fragment export; manual import (`hermes/fragments/config.yaml`) | `agents-md`; instructions `available`; skills `available` | user; `~/.hermes/config.yaml` | `manual` | checked 2026-10-26; runtime pending |
| `jetbrains-air` | `desktop` | Native staged (`jetbrains-air/.air/mcp.json`) | `agents-md`; instructions `available`; skills `available` | project; `.air/mcp.json` | `manual` | checked 2026-10-26; runtime pending |
| `junie` | `cli` | Native staged (`junie/.junie/mcp/mcp.json`) | `agents-md`; instructions `available`; skills `available` | project; `.junie/mcp/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `kilo` | `cli` | Native staged (`kilo/.kilo/kilo.json`) | `agents-md`; instructions `available`; skills `available` | project; `.kilo/kilo.json` | `manual` | checked 2026-10-22; runtime pending |
| `kilo` | `ide` | Native staged (`kilo/.kilo/kilo.json`) | `agents-md`; instructions `available`; skills `available` | project; `.kilo/kilo.json` | `manual` | checked 2026-10-22; runtime pending |
| `kimi-code` | `cli` | Native staged (`kimi-code/.kimi-code/mcp.json`) | `agents-md`; instructions `available`; skills `available` | project; `.kimi-code/mcp.json` | `manual` | checked 2026-10-26; runtime pending |
| `kiro` | `ide` | Native staged (`kiro/.kiro/settings/mcp.json`) | `steering`; instructions `available`; skills `available` | project; `.kiro/settings/mcp.json` | `unknown` | checked 2026-10-22; runtime pending |
| `lm-studio` | `desktop` | Fragment export; manual import (`lm-studio/fragments/mcp.json`) | bundle gap (instructions `unknown`; skills `unknown`) | user; `~/.lmstudio/mcp.json` | `unknown` | checked 2026-10-26; runtime pending |
| `lm-studio` | `bionic` | Unknown | `manual`; instructions `manual`; skills `manual` | app; no native repository path declared | `unknown` | checked 2026-10-26; runtime pending |
| `lm-studio` | `api` | Request fragment (`lm-studio/requests/api.json`) | `manual`; instructions `manual`; skills `unknown` | request; http://127.0.0.1:1234/api/v1/chat | `available` | checked 2026-10-26; runtime pending |
| `opencode` | `cli` | Native staged (`opencode/opencode.json`) | `agents-md`; instructions `available`; skills `available` | project; `opencode.json` | `manual` | checked 2026-10-22; runtime pending |
| `openhands` | `cli` | Fragment export; manual import (`openhands/fragments/mcp.json`) | `agents-md`; instructions `available`; skills `available` | user; ~/.openhands/mcp.json | `manual` | checked 2026-10-22; runtime pending |
| `perplexity` | `app` | Not exported; manual interface | bundle gap (instructions `unknown`; skills `unknown`) | app; no native repository path declared | `unknown` | checked 2026-10-26; runtime pending |
| `perplexity` | `agent-api` | Request fragment (`perplexity/requests/agent-api.json`) | `manual`; instructions `manual`; skills `manual` | request; https://api.perplexity.ai/v1/agent | `available` | checked 2026-10-26; runtime pending |
| `perplexity` | `orchestrator` | Unknown | bundle gap (instructions `unknown`; skills `unknown`) | none; https://api.perplexity.ai/orchestrator/v1 | `available` | checked 2026-10-26; runtime pending |
| `pi` | `cli` | Native staged (`pi/.pi/mcp.json`) | `agents-md`; instructions `available`; skills `available` | project; `.pi/mcp.json` | `manual` | checked 2026-10-22; runtime pending |
| `qoder` | `cli` | Native staged (`qoder/.qoder/settings.json`) | `rules`; instructions `available`; skills `available` | project; `.qoder/settings.json` | `unknown` | checked 2026-10-22; runtime pending |
| `qoder` | `ide` | Unknown | bundle gap (instructions `unknown`; skills `unknown`) | none; no native repository path declared | `manual` | checked 2026-10-22; runtime pending |
| `qwen-code` | `cli` | Native staged (`qwen-code/.qwen/settings.json`) | `qwen-md`; instructions `available`; skills `available` | project; `.qwen/settings.json` | `manual` | checked 2026-10-26; runtime pending |
| `zed` | `ide` | Fragment export; manual import (`zed/fragments/settings.json`) | `rules`; instructions `available`; skills `available` | user; Zed user settings.json | `manual` | checked 2026-10-22; runtime pending |

Qoder IDE remains a provider handoff surface with a bundle gap. Codex CLI, desktop, and IDE share documented project configuration and Skills paths, with separate evidence records. JetBrains Air and JetBrains Junie remain distinct products. Current Kimi Code exports target `.kimi-code`; legacy `.kimi` configuration is not silently migrated or overwritten. Qwen HTTP uses `httpUrl`; its `url` spelling denotes the separately documented SSE transport.

ChatGPT desktop, web, Work local, and Work cloud use app-managed manual handoffs. An OpenAI inference API is a separate provider and does not imply ChatGPT Work model or connector access. Perplexity app MCP is a manual application flow; Agent API MCP and Skills are request-level interfaces. LM Studio desktop MCP is a user-settings fragment, Bionic Skills use a manual application flow, and LM Studio API MCP is an explicit request fragment. None is evidence of a native mobile or installed-client route.

## MCP transports and stdio staging

The catalog accepts `kind: remote` with a credential-free HTTPS URL, or `kind: stdio` with one executable string and a structured `args` array. Both require `default_enabled: false`, profile permission, and an explicit `--enable` selection. Remote URL exports retain their existing formats. The following 22 products have separately documented stdio serialization for 25 matching registry surfaces; this table is documentation and fixture evidence, not installed-client evidence. A product with several surfaces uses stdio only where its registered dialect matches the documented route.

| Product | Registered dialect | Stdio schema | Evidence |
|---|---|---|---|
| `abacus` | `mcpServers.url` | `command-args` | checked 2026-10-26; [documentation](https://abacus.ai/help/abacusai-desktop/mcp-configuration); runtime pending |
| `amp` | `amp.mcpServers.url` | `command-args` | checked 2026-10-26; [documentation](https://ampcode.com/docs/customize/mcp); runtime pending |
| `antigravity` | `mcpServers.serverUrl` | `command-args` | checked 2026-10-26; [documentation](https://antigravity.google/docs/mcp); runtime pending |
| `claude-code` | `mcpServers.type-http` | `command-args` | checked 2026-10-26; [documentation](https://code.claude.com/docs/en/mcp); runtime pending |
| `cline` | `mcpServers.type-streamableHttp` | `command-args` | checked 2026-10-26; [documentation](https://docs.cline.bot/mcp/mcp-overview); runtime pending |
| `codex` | `mcp_servers.toml-url` | `toml-command-args` | checked 2026-10-26; [documentation](https://learn.chatgpt.com/docs/extend/mcp); runtime pending |
| `copilot` | `servers.type-http` | `type-stdio` | checked 2026-10-26; [documentation](https://code.visualstudio.com/docs/agents/reference/mcp-configuration); runtime pending |
| `cursor` | `mcpServers.url` | `type-stdio` | checked 2026-10-26; [documentation](https://cursor.com/docs/mcp); runtime pending |
| `devin` | `mcpServers.transport-http` | `command-args` | checked 2026-10-26; [documentation](https://docs.devin.ai/cli/extensibility/mcp/configuration); runtime pending |
| `goose` | `extensions.uri-streamable_http` | `goose-stdio` | checked 2026-10-26; [documentation](https://goose-docs.ai/docs/guides/config-files/); runtime pending |
| `junie` | `mcpServers.type-http` | `command-args` | checked 2026-10-26; [documentation](https://junie.jetbrains.com/docs/junie-cli-mcp-configuration.html); runtime pending |
| `kilo` | `mcp.type-remote` | `local-command-array` | checked 2026-10-26; [documentation](https://kilo.ai/docs/automate/mcp/using-in-cli); runtime pending |
| `kiro` | `mcpServers.url` | `command-args` | checked 2026-10-26; [documentation](https://kiro.dev/docs/mcp/configuration/); runtime pending |
| `opencode` | `mcp.type-remote` | `local-command-array` | checked 2026-10-26; [documentation](https://opencode.ai/docs/mcp-servers/); runtime pending |
| `pi` | `mcpServers.url-or-type-http` | `command-args` | checked 2026-10-26; [documentation](https://pi.dev/docs/latest/mcp); runtime pending |
| `qoder` | `mcpServers.type-http` | `command-args` | checked 2026-10-26; [documentation](https://docs.qoder.com/cli/mcp-reference); runtime pending |
| `zed` | `context_servers.url` | `command-args` | checked 2026-10-26; [documentation](https://zed.dev/docs/ai/mcp); runtime pending |
| `factory` | `mcpServers.type-http` | `type-stdio` | checked 2026-10-26; [documentation](https://docs.factory.com/harness/mcp); runtime pending |
| `hermes` | `mcp_servers.yaml-url-enabled` | `hermes-stdio` | checked 2026-10-26; [documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp); runtime pending |
| `kimi-code` | `mcpServers.url` | `command-args` | checked 2026-10-26; [documentation](https://moonshotai.github.io/kimi-code/en/customization/mcp.html); runtime pending |
| `lm-studio` | `mcpServers.url` | `type-stdio` | checked 2026-10-26; [LM Studio documentation](https://lmstudio.ai/docs/app/mcp), [compatible JSON fields](https://cursor.com/docs/mcp); runtime pending |
| `qwen-code` | `mcpServers.httpUrl` | `command-args` | checked 2026-10-26; [documentation](https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/); runtime pending |

JetBrains Air and OpenHands retain stdio file-export gaps because their exact local-file schemas are not established here. Perplexity Agent API and LM Studio ephemeral MCP API request bodies are remote HTTPS only and reject selected stdio entries. Manual app-managed routes do not receive a guessed local launch configuration.

A custom catalog in an explicit `--root` may contain this illustrative, uninstalled entry:

```json
{
  "servers": {
    "reviewed-stdio": {
      "kind": "stdio",
      "command": "reviewed-mcp-executable",
      "args": ["--example"],
      "default_enabled": false
    }
  }
}
```

The example describes an argv structure; it does not identify an installed program. Add a reviewed entry to the chosen profile's `allowed_mcp` and select its name explicitly when staging. The executable is bounded to 1,024 UTF-8 bytes, with at most 128 arguments, 4,096 bytes per argument, and 32,768 argument bytes in total. Raw shell-command fields, environment maps, permission fields, variable expansion, control characters, and credential-looking values are refused. These checks validate the artifact's structure and selected input patterns; they do not establish trust in a future executable.

Weaver never executes the command, invokes a shell, installs a package, expands environment variables, or starts an MCP server. Some vendor formats require an enabled marker for an explicitly selected staged entry; that marker does not mean Weaver launched or activated it. Review the artifact before any manual import. The bundled remote `perplexity` entry points to the official hosted MCP service and remains default-off; existing profiles do not automatically allow it. That MCP service is distinct from the Perplexity app, Agent API, and Orchestrator model routes.

## Provider surfaces

| Product | Surface | API base | Endpoint | Environment reference | Evidence |
|---|---|---|---|---|---|
| `anthropic` | `api` | `https://api.anthropic.com` | `/v1/messages` | `ANTHROPIC_API_KEY` | checked 2026-10-26; runtime pending |
| `google` | `api` | `https://generativelanguage.googleapis.com` | `/v1beta` | `GEMINI_API_KEY` | checked 2026-10-26; runtime pending |
| `openai` | `api` | `https://api.openai.com/v1` | `/responses` | `OPENAI_API_KEY` | checked 2026-10-26; runtime pending |
| `openrouter` | `gateway` | `https://openrouter.ai/api/v1` | `/chat/completions` | `OPENROUTER_API_KEY` | checked 2026-10-22; runtime pending |

Provider IDs and surfaces come from the registry. Provider metadata requires explicit model selection and keeps `mcp_service_enabled: false`. HTTPS URLs contain no credentials, query, or fragment. Only literal local LM Studio loopback endpoints may use HTTP. An absent documented environment name stays `null`; Weaver never invents or reads a secret value.

## Operating-system workflows

| OS family | Contract in this release | Evidence |
|---|---|---|
| `windows` | Primary CI target for the core CLI on Python 3.11+ (GitHub matrix) | Core CLI CI recorded for Python 3.11–3.13; see [readiness evidence](readiness.md#recorded-core-cli-ci-evidence). Native vendor runtime pending |
| `macos` | Primary CI target for the core CLI on Python 3.11+ (GitHub matrix) | Core CLI CI recorded for Python 3.11–3.13; see [readiness evidence](readiness.md#recorded-core-cli-ci-evidence). Native vendor runtime pending |
| `linux` | Primary CI target for the core CLI on Python 3.11+ (GitHub and GitLab matrices) | GitHub core CLI CI recorded for Python 3.11–3.13; see [readiness evidence](readiness.md#recorded-core-cli-ci-evidence). Native vendor runtime pending |
| `bsd` | Conditional CLI target. Variants freebsd, openbsd, netbsd, and dragonflybsd are named separately; Python 3.11+ on a real host is pending. Evidence never crosses variants | pending |
| `solaris` | Conditional CLI target. Variants solaris and illumos are named separately; Python 3.11+ on a real host is pending | pending |
| `android` | Conditional Termux or compatible Python core workflow, plus a remote handoff. Not a general native CLI or phone-local agent claim | pending |
| `ios` | Remote and artifact handoff only, through a separate supported host. No on-device CLI or native mobile application is provided | pending |

Only Windows, macOS, and Linux are primary CI targets. A cross-OS unit simulation is detector coverage, not runtime evidence.

The recorded core CLI CI run builds and installs Weaver's own distributions and runs offline smoke checks. It is historical evidence for the earlier 20-product source commit, not verification of this 31-product expansion. It does not install or verify any vendor agent. Its evidence is recorded separately from the registry's vendor capability `runtime_checks`; those remain pending. CI runner images are specific environments and do not establish support for every OS variant or architecture.

## OpenRouter provider handoffs

`provider` requires `--provider openrouter`, an explicit `--agent`, and `--model`. It uses the declared default surface unless `--surface` is supplied. No model is selected automatically. Each successful handoff writes `<agent>/provider/provider-setup.md`: setup steps, the model convention, endpoint, documented environment variable **names**, restrictions, and sources. Codex additionally writes a native TOML fragment at `codex/provider/config.toml` that names `env_key = "OPENROUTER_API_KEY"` and contains no credential.

Provider setup never reads credential environment-variable values or credential storage. Generated text names variables only. OS detection may consult platform indicators such as WSL and Termux markers. API-key values are never accepted as arguments, and no provider secrets configuration is guessed. Credential storage guarantees are vendor-specific; local config/authentication files are not claimed to be OS-keychain-backed.

| Agent | Surface | Result | Artifacts |
|---|---|---|---|
| `abacus` | `desktop` | Handoff | `abacus/provider/provider-setup.md` |
| `aider` | `cli` | Handoff | `aider/provider/provider-setup.md` |
| `amp` | `cli` | Handoff | `amp/provider/provider-setup.md` |
| `antigravity` | `ide` | Documented gap | none |
| `chatgpt` | `desktop` | Documented gap | none |
| `chatgpt` | `web` | Documented gap | none |
| `chatgpt` | `work-local` | Documented gap | none |
| `chatgpt` | `work-cloud` | Documented gap | none |
| `claude-code` | `cli` | Handoff | `claude-code/provider/provider-setup.md` |
| `cline` | `ide` | Handoff | `cline/provider/provider-setup.md` |
| `codex` | `cli` | Handoff | `codex/provider/provider-setup.md`, `codex/provider/config.toml` |
| `codex` | `desktop` | Handoff | `codex/provider/provider-setup.md`, `codex/provider/config.toml` |
| `codex` | `ide` | Handoff | `codex/provider/provider-setup.md`, `codex/provider/config.toml` |
| `copilot` | `vscode` | Handoff | `copilot/provider/provider-setup.md` |
| `cursor` | `ide` | Handoff | `cursor/provider/provider-setup.md` |
| `devin` | `desktop-local` | Documented gap | none |
| `factory` | `cli` | Handoff | `factory/provider/provider-setup.md` |
| `goose` | `cli` | Handoff | `goose/provider/provider-setup.md` |
| `hermes` | `cli` | Handoff | `hermes/provider/provider-setup.md` |
| `jetbrains-air` | `desktop` | Documented gap | none |
| `junie` | `cli` | Handoff | `junie/provider/provider-setup.md` |
| `kilo` | `cli` | Handoff | `kilo/provider/provider-setup.md` |
| `kilo` | `ide` | Handoff | `kilo/provider/provider-setup.md` |
| `kimi-code` | `cli` | Handoff | `kimi-code/provider/provider-setup.md` |
| `kiro` | `ide` | Documented gap | none |
| `lm-studio` | `desktop` | Documented gap | none |
| `lm-studio` | `bionic` | Documented gap | none |
| `lm-studio` | `api` | Documented gap | none |
| `opencode` | `cli` | Handoff | `opencode/provider/provider-setup.md` |
| `openhands` | `cli` | Handoff | `openhands/provider/provider-setup.md` |
| `perplexity` | `app` | Documented gap | none |
| `perplexity` | `agent-api` | Documented gap | none |
| `perplexity` | `orchestrator` | Documented gap | none |
| `pi` | `cli` | Handoff | `pi/provider/provider-setup.md` |
| `qoder` | `cli` | Documented gap | none |
| `qoder` | `ide` | Handoff | `qoder/provider/provider-setup.md` |
| `qwen-code` | `cli` | Handoff | `qwen-code/provider/provider-setup.md` |
| `zed` | `ide` | Handoff | `zed/provider/provider-setup.md` |

A gap in this legacy OpenRouter command does not establish that the product rejects every provider. Use the model/provider route inventory for separate documented protocols. Qoder CLI does not inherit its IDE route.

## Instruction and skill bundles

- Instruction files go to `<agent>/<documented instruction path>` only where the registry records an available instruction mechanism.
- Abacus uses the documented root `AGENTS.md` only. There is no native skill directory for Abacus, so its selected skills are manual read files.
- A surface without a documented project instruction target receives `<agent>/handoff/INSTRUCTIONS.md` with vendor-sourced manual steps. No native path is guessed.
- For manual handoffs and for Aider, selected skills are staged as `<agent>/read/<name>.md` and linked from the instruction file. These are portable read files, not native discovery.
- Where the registry records an available native skill path, selected skills are copied to `<agent>/<skills path>/<name>/SKILL.md` with canonical LF bytes. CRLF checkout line endings are normalized; skill content is otherwise unchanged.
- Codex output above the documented 32 KiB limit and Abacus output above 40,000 characters are refused. Content is never truncated.
- Profile guidance is advisory and does not enforce tool use.

## Requested scope and aliases

`coverage --format json` reports all 23 requested groups from `catalogs/requested-coverage.json`, mapped to 33 distinct registry surfaces. A mapped disposition records that a request was considered; it is not a native support result. Each target reports capability states, missing documentation, fixture references, and runtime evidence separately. Unknown entries remain visible. `coverage --strict` fails while required evidence remains unresolved; it never upgrades a manual or unknown route.

The JSON also includes model/provider inventory, custom-agent outcomes, and `mcp_transports.stdio` documentation metadata. The latter lists the 22 documented stdio adapters, unresolved file-export routes, and remote-only request exclusions. Its runtime status remains unverified; those records describe staged formats rather than an executed server.

Aliases are explicit, case-insensitive catalog entries. For example, `OpenAIcodex` resolves to `codex`, `kiloAI` to `kilo`, `FactoryDroid` to `factory`, and `LMStudio` to `lm-studio`. `JetBrainsAir` resolves to `jetbrains-air`, never `junie`. Unknown names and colliding aliases fail closed. `VSCodeagents` is an explicit group containing Copilot's VS Code surface and the Cline, Kilo, and Codex extension surfaces. Use `coverage` to inspect it and select a concrete product for rendering; it is not an alias for every extension available in VS Code.

## Models and reasoning

`catalogs/model-providers.json` defines documented provider routes separately from product identity. Its model entries are dated examples with capability evidence, not an exhaustive allowlist. An explicit model ID may be accepted without appearing in those examples. Acceptance does not establish visibility, successful inference, native execution, or account availability. No vendor entitlement is inferred from a product subscription or a model listing.

| Provider route | Registry product / surface | Protocols |
|---|---|---|
| `openrouter` | `openrouter` / `gateway` | `openrouter-chat`, `openai-chat` |
| `openai` | `openai` / `api` | `openai-responses`, `openai-chat` |
| `anthropic` | `anthropic` / `api` | `anthropic-messages` |
| `google` | `google` / `api` | `google-generate-content` |
| `lm-studio` | `lm-studio` / `api` | `lmstudio-chat`, `openai-chat`, `openai-responses`, `anthropic-messages` |
| `perplexity-agent` | `perplexity` / `agent-api` | `perplexity-agent` |
| `perplexity-orchestrator` | `perplexity` / `orchestrator` | `openai-chat`, `openai-responses`, `anthropic-messages` |

`models --provider ID` describes the route and discovery interface without contacting it. Add `--input snapshot.json` to normalize a response captured separately by the operator. Snapshots may contain one provider response or an explicit `pages` list plus optional provenance (`source_url`, `captured_on`, `scope`, `auth_scope`). Every supplied page is processed; the command does not fetch omitted pages. A bounded local file limit prevents unbounded inputs. Provenance records a SHA-256 of the input and omits its local path. Visibility in that snapshot does not establish current access or successful inference. Catalog completeness stays unknown. Keep real account snapshots and private provenance out of commits.

`models --agent PRODUCT --surface SURFACE` inspects host-specific model selection and reasoning controls, with explicit unknown availability. Product aliases resolve through the requested-scope catalog. `--surface` requires `--agent`; snapshot `--input` cannot be combined with `--agent`, because a provider listing and a host control inventory are separate inputs.

`model` requires an explicit `--agent`, `--provider`, and `--model`; `--surface` overrides the product default. Use `--catalog snapshot.json` for a separately captured provider response, `--protocol` to select a documented protocol, and `--context-size` only with an established model limit. Output includes `<product>/model/model-setup.md` and `<product>/model/model-selection.json`. Qwen Code CLI can also receive `settings.fragment.json` for documented OpenAI-compatible routes. Kimi Code CLI can receive `config.fragment.toml` for documented OpenAI or Anthropic routes; a supplied or snapshot context size is required for a complete native fragment, and otherwise the output remains a manual template. Other product/surface/provider combinations remain manual handoffs unless explicitly implemented.

Those native model fragments require a documented credential environment reference and a representable configuration. LM Studio has no established environment name in this catalog, so Qwen/Kimi setup through that local provider stays manual. An OpenAI reasoning mode is not silently inserted into Qwen or Kimi configuration. Kimi template output cannot represent a reasoning token budget or the `none`/`minimal` effort labels; these combinations retain a manual handoff rather than an invented native setting.

Reasoning controls retain their vendor meaning: `--reasoning-effort`, `--reasoning-budget`, `--reasoning on|off`, and `--reasoning-mode`. An effort label, token budget, toggle, or mode requires matching model capability evidence; an unknown explicit reasoning request fails. OpenAI Responses `reasoning.effort`, OpenAI Chat `reasoning_effort`, Anthropic thinking/output settings, Google thinking configuration, LM Studio native chat's reasoning string, and OpenRouter's reasoning object are different wire formats. A documented value for one model or protocol is not transferred to another. The independently documented OpenAI `standard`/`pro` mode applies only to the exact entries and Responses route recorded in the catalog. Perplexity Agent API's top-level effort preset remains distinct from a selected model's reasoning effort.

The model commands never read credential environment-variable values, contact discovery endpoints, run inference, or make paid calls. Omitted or unavailable token/cost information stays unknown, not zero. A snapshot is caller-supplied evidence; it does not turn into installed-client or native-device evidence.

## Host model controls

`catalogs/model-hosts.json` records one model-selection and reasoning-control row for each of the 42 registered surfaces. Inspect these rows with `models --agent PRODUCT --surface SURFACE`. They describe a documented host picker, request field, session command, or configuration mechanism; they do not enable a native exporter. Their `manual` state is documentation about an operator-controlled setting, independent of the native/manual model-setup artifact contract above. Every row keeps account/model availability unknown and runtime checks pending.

Null control values mean the allowed values must be discovered for the selected host and model. Even listed values are model-dependent; they are not a promise that every account, model, or installed version offers them. Model choosers can change without changing a product's identity. A host's session picker does not establish its API catalog, and a provider snapshot does not establish its app subscription catalog. Work Power presets, Amp Dial presets, Devin model variants, and Perplexity Agent presets preserve their own meaning rather than becoming a common reasoning-effort enum.

| Product | Surface | Model selection | Reasoning controls | Availability / evidence |
|---|---|---|---|---|
| `abacus` | `desktop` | `manual`: Choose a Desktop model through the configured provider; CoWork effort presets are host routing choices. Commands: `/providers`, `/model`. | `manual`: `variant` / `Desktop CoWork effort selector`: `Low`, `Medium`, `High` | `unknown`; checked 2026-10-26; runtime pending |
| `aider` | `cli` | `manual`: Select an explicit provider/model in the CLI; reasoning flags apply only to models accepting those settings. Fields: `model`. Commands: `--model`, `/model`, `--reasoning-effort`, `--thinking-tokens`. | `manual`: `effort` / `--reasoning-effort`: values require model-specific discovery; `budget` / `--thinking-tokens`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `amp` | `cli` | `manual`: Choose a Dial preset or configure model routing; each Dial preset combines a model with reasoning and other host behavior. Commands: `amp config model-providers`. UI: Choose the Dial mode. | `manual`: `variant` / `Dial mode`: `low`, `medium`, `high`, `ultra` | `unknown`; checked 2026-10-26; runtime pending |
| `anthropic` | `api` | `manual`: Set model in Messages requests; authenticated paginated model discovery is distinct from host subscription catalogs. Fields: `model`. | `manual`: `effort` / `output_config.effort`: values require model-specific discovery; `budget` / `thinking.budget_tokens`: values require model-specific discovery; `mode` / `thinking.type`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `antigravity` | `ide` | `manual`: Use the conversation model selector; supported thinking levels depend on the selected model and IDE availability. UI: Open the model selector under the conversation prompt; Choose an available model and supported thinking level. | `manual`: `effort` / `Thinking level`: `Low`, `Medium`, `High` | `unknown`; checked 2026-10-26; runtime pending |
| `chatgpt` | `desktop` | `manual`: Inspect the Work model and reasoning picker; its signed-in account/workspace catalog is separate from OpenAI API discovery. UI: Open the model and reasoning control beneath the composer; Use Advanced to inspect available models and reasoning options. | `manual`: `effort` / `Work reasoning effort`: values require model-specific discovery; `variant` / `Power preset`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `chatgpt` | `web` | `manual`: Inspect the Work model and reasoning picker; its signed-in account/workspace catalog is separate from OpenAI API discovery. UI: Open the model and reasoning control beneath the composer; Use Advanced to inspect available models and reasoning options. | `manual`: `effort` / `Work reasoning effort`: values require model-specific discovery; `variant` / `Power preset`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `chatgpt` | `work-cloud` | `manual`: Inspect the Work model and reasoning picker; its signed-in account/workspace catalog is separate from OpenAI API discovery. UI: Open the model and reasoning control beneath the composer; Use Advanced to inspect available models and reasoning options. | `manual`: `effort` / `Work reasoning effort`: values require model-specific discovery; `variant` / `Power preset`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `chatgpt` | `work-local` | `manual`: Inspect the Work model and reasoning picker; its signed-in account/workspace catalog is separate from OpenAI API discovery. UI: Open the model and reasoning control beneath the composer; Use Advanced to inspect available models and reasoning options. | `manual`: `effort` / `Work reasoning effort`: values require model-specific discovery; `variant` / `Power preset`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `claude-code` | `cli` | `manual`: Use the model picker or an explicit model setting; session and persistent effort scopes differ and available levels depend on the model. Fields: `model`, `effortLevel`, `modelSettings`. Commands: `/model`, `--model`, `/effort`, `--effort`. | `manual`: `effort` / `/effort or --effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `cline` | `ide` | `manual`: Choose a provider and model in Cline settings; subscription sign-in and API providers have different catalogs. UI: Open Cline settings; Choose API Provider and a model. | `manual`: `toggle` / `Enable Extended Thinking`: `true`, `false` | `unknown`; checked 2026-10-26; runtime pending |
| `codex` | `cli` | `manual`: Choose a session model or set model in trusted configuration; provider definitions belong in user-level config because project-local provider keys are ignored. Fields: `model`, `model_reasoning_effort`. Commands: `/model`. | `manual`: `effort` / `model_reasoning_effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `codex` | `desktop` | `manual`: Choose a session model or set model in trusted configuration; provider definitions belong in user-level config because project-local provider keys are ignored. Fields: `model`, `model_reasoning_effort`. UI: Open the model and reasoning control beneath the composer. | `manual`: `effort` / `model_reasoning_effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `codex` | `ide` | `manual`: Choose a session model or set model in trusted configuration; provider definitions belong in user-level config because project-local provider keys are ignored. Fields: `model`, `model_reasoning_effort`. UI: Open the model and reasoning control beneath the composer. | `manual`: `effort` / `model_reasoning_effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `copilot` | `vscode` | `manual`: Use VS Code chat model selection; signed-in managed models and configured BYOK models remain separate access paths. UI: Open the chat model picker; For a supported model open its Thinking Effort submenu. | `manual`: `effort` / `Thinking Effort submenu`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `cursor` | `ide` | `manual`: Select a model in the account-scoped picker; effort choices and model parameters depend on the model and account policy. Fields: `model`. UI: Open the model picker; Inspect the selected model parameters. | `manual`: `effort` / `Model effort selector`: values require model-specific discovery; `effort` / `Subagent model ID [effort=value]`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `devin` | `desktop-local` | `manual`: Choose the session model from Devin Desktop; SWE-2 variants are model choices rather than a universal provider effort enum. UI: Open the session model dropdown. | `manual`: `variant` / `SWE-2 variant`: `Medium`, `High`, `Max` | `unknown`; checked 2026-10-26; runtime pending |
| `factory` | `cli` | `manual`: Select a built-in or custom model; custom model thinking settings depend on provider and model recognition. Fields: `customModels[].model`. Commands: `/model`, `/settings`. | `manual`: `effort` / `customModels[].reasoningEffort`: values require model-specific discovery; `toggle` / `customModels[].enableThinking`: `true`, `false`; `budget` / `customModels[].thinkingMaxTokens`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `google` | `api` | `manual`: Choose a model resource from authenticated model discovery; protocol-specific reasoning fields must retain their request shape. Fields: `model`. | `manual`: `effort` / `GenerateContent generationConfig.thinkingConfig.thinkingLevel`: values require model-specific discovery; `budget` / `GenerateContent generationConfig.thinkingConfig.thinkingBudget`: values require model-specific discovery; `effort` / `Interactions generation_config.thinking_level`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `goose` | `cli` | `manual`: Select a configured provider/model; provider-specific environment controls are not a common effort vocabulary. Fields: `GOOSE_PROVIDER`, `GOOSE_MODEL`. Commands: `/model`. | `manual`: `effort` / `GEMINI3_THINKING_LEVEL`: `low`, `high`; `mode` / `CLAUDE_THINKING_TYPE`: `adaptive`, `enabled`, `disabled` | `unknown`; checked 2026-10-26; runtime pending |
| `hermes` | `cli` | `manual`: Choose a configured model or use the session picker; model-specific reasoning overrides remain distinct from the dashboard catalog. Fields: `model`, `agent.reasoning_effort`, `agent.reasoning_overrides`. Commands: `/model`, `/reasoning`, `/reasoning --global`. | `manual`: `effort` / `agent.reasoning_effort`: values require model-specific discovery; `toggle` / `agent.reasoning_effort.enabled`: `true`, `false` | `unknown`; checked 2026-10-26; runtime pending |
| `jetbrains-air` | `desktop` | `manual`: Choose the agent and its model in the task toolbar; provider and agent choices determine the available model list. A shared host reasoning override has not been established. UI: Choose the agent and model in the new-task toolbar; Inspect Settings > AI Providers. | `unknown`; no established reasoning override | `unknown`; checked 2026-10-26; runtime pending |
| `junie` | `cli` | `manual`: Use the session model picker and supported effort picker; the account catalog and Default selection can change. Commands: `/model`, `/effort`. | `manual`: `effort` / `/effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `kilo` | `cli` | `manual`: Choose the configured provider/model and one of its supported reasoning variants; output thinking visibility is a separate display setting. Fields: `model`. Commands: `/models`, `/variant`, `--model`, `--variant`. | `manual`: `variant` / `Reasoning variant`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `kilo` | `ide` | `manual`: Choose the configured provider/model and one of its supported reasoning variants; output thinking visibility is a separate display setting. Fields: `model`. UI: Open the chat model picker; Choose a supported reasoning variant. | `manual`: `variant` / `Reasoning variant`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `kimi-code` | `cli` | `manual`: Choose a configured model alias; native provider setup requires an explicit known context size and supported reasoning capabilities. Fields: `default_model`, `models.<alias>.model`, `models.<alias>.max_context_size`. Commands: `/model`. | `manual`: `toggle` / `thinking.enabled`: `true`, `false`; `effort` / `thinking.effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `kiro` | `ide` | `manual`: Choose a managed model in the IDE; the account model catalog and available effort slider levels are host controlled. UI: Open the IDE model picker; Adjust Effort below the model list when offered. | `manual`: `effort` / `IDE Effort slider`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `lm-studio` | `api` | `manual`: Discover locally available model keys with the native models endpoint; local listing and loaded/runtime availability are separate facts. Fields: `model`. | `manual`: `variant` / `Native /api/v1/chat reasoning`: values require model-specific discovery; `effort` / `Responses reasoning.effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `lm-studio` | `bionic` | `manual`: Choose a cloud, local or remote model per session or set a default; configurable reasoning levels appear only for supported models. UI: Open the session model picker; For a supported model choose a reasoning level. | `manual`: `effort` / `Session reasoning level`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `lm-studio` | `desktop` | `manual`: Choose a downloaded model in the desktop app; reasoning settings depend on that model and runtime capabilities. UI: Choose a model from the desktop model loader; Inspect supported model reasoning settings. | `manual`: `variant` / `Model reasoning setting`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `openai` | `api` | `manual`: Set model in an API request; model-list visibility is scoped to API credentials and project access, independently of ChatGPT hosts. Fields: `model`. | `manual`: `effort` / `Responses reasoning.effort`: values require model-specific discovery; `effort` / `Chat Completions reasoning_effort`: values require model-specific discovery; `mode` / `Responses reasoning.mode`: `standard`, `pro` | `unknown`; checked 2026-10-26; runtime pending |
| `opencode` | `cli` | `manual`: Choose a configured provider/model; reasoning options and named variants are model dependent. Fields: `model`, `provider.<id>.models.<id>.options`, `provider.<id>.models.<id>.variants`. Commands: `/models`. | `manual`: `effort` / `provider.<id>.models.<id>.options.reasoningEffort`: values require model-specific discovery; `variant` / `provider.<id>.models.<id>.variants`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `openhands` | `cli` | `manual`: Use the CLI first-run LLM configuration. Its README marks this CLI unmaintained; Agent Canvas and SDK controls do not establish CLI reasoning settings. Fields: `~/.openhands/agent_settings.json`. Commands: `openhands`. | `unknown`; no established reasoning override | `unknown`; checked 2026-10-26; runtime pending |
| `openrouter` | `gateway` | `manual`: Set an explicit gateway model ID and inspect the gateway model-list response; account and downstream provider access may differ. Fields: `model`. | `manual`: `effort` / `reasoning.effort`: values require model-specific discovery; `budget` / `reasoning.max_tokens`: values require model-specific discovery; `toggle` / `reasoning.enabled`: `true`, `false`; `mode` / `reasoning.mode`: `standard`, `pro` | `unknown`; checked 2026-10-26; runtime pending |
| `perplexity` | `agent-api` | `manual`: Choose an Agent API model or an effort preset; presets select model bundles and do not constitute a universal reasoning setting. Fields: `model`, `effort`. | `manual`: `variant` / `Agent API effort preset`: `fast`, `low`, `medium`, `high`, `xhigh`; `effort` / `reasoning.effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `perplexity` | `app` | `manual`: Inspect the signed-in app model selector; available models depend on its account and organization, independently of API model lists. A separate reasoning control has not been established. UI: Open the model selector beneath the search bar. | `unknown`; no established reasoning override | `unknown`; checked 2026-10-26; runtime pending |
| `perplexity` | `orchestrator` | `manual`: Choose an Orchestrator allowlisted model; authenticated discovery and request shape are specific to this API, separate from the app. Fields: `model`. | `manual`: `effort` / `Chat Completions reasoning_effort`: values require model-specific discovery; `effort` / `Responses reasoning.effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `pi` | `cli` | `manual`: Choose a model from the configured and authenticated catalog, then select a thinking level supported by that model. Commands: `/model`, `/thinking`. | `manual`: `effort` / `/thinking`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `qoder` | `cli` | `manual`: Discover CLI model IDs with the documented listing command or session picker; effort and context controls remain model dependent. Commands: `--list-models`, `--model`, `/model`, `--reasoning-effort`, `/effort`. | `manual`: `effort` / `--reasoning-effort or /effort`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `qoder` | `ide` | `manual`: Choose a model in AI Chat and manage visible or custom models in IDE settings; configure thinking effort only when the model offers it. UI: Open the AI Chat model picker; Open Settings > Models to inspect model parameters. | `manual`: `effort` / `Thinking effort parameter`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |
| `qwen-code` | `cli` | `manual`: Choose a configured model provider entry; reasoning request parameters depend on the selected model and provider protocol. Fields: `model.name`, `modelProviders`. Commands: `/model`. | `manual`: `effort` / `generationConfig.reasoning.effort`: values require model-specific discovery; `budget` / `generationConfig.reasoning.budget_tokens`: values require model-specific discovery; `toggle` / `generationConfig.reasoning`: `false` | `unknown`; checked 2026-10-26; runtime pending |
| `zed` | `ide` | `manual`: Select a model in a native Zed Agent thread; external agents and terminal threads own their own model settings. Fields: `agent.default_model`, `agent.subagent_model`. UI: Open the native Zed Agent model selector; Inspect Change Thinking Effort for the selected model. | `manual`: `effort` / `agent.default_model.effort`: values require model-specific discovery; `toggle` / `agent.default_model.enable_thinking`: `true`, `false`; `budget` / `language_models.<provider>.available_models[].mode.budget_tokens`: values require model-specific discovery | `unknown`; checked 2026-10-26; runtime pending |

JetBrains Air desktop, OpenHands CLI, and Perplexity app retain unknown reasoning overrides. Antigravity's documented IDE thinking levels are specific to its model selector; a command documented only for a different CLI is not transferred to that IDE. Zed controls above apply to its native Agent threads; external agents retain their own settings. Kilo's reasoning variant and thinking-output visibility are distinct controls. Codex provider definitions belong in user-level configuration; project-local provider keys are ignored. The generated staging directory is never permission to replace existing user settings.

## Custom agent definitions

`agents inventory --format json` describes each product/surface's custom-agent definition route independently of MCP, project instructions, portable Skills, and model selection. Use `--agent PRODUCT` to inspect a concrete product. Native definition, manual handoff, unknown, and unsupported outcomes remain distinct; a product's general agent capability does not imply that it loads arbitrary agent files.

`agent --agent PRODUCT --name NAME --description TEXT --instructions-file instructions.md` stages a definition only through an implemented documented route. Supply `--surface` when needed and optional `--model`, `--provider`, `--catalog`, or `--reasoning-effort` only where that definition format supports them. Each successful export includes `<product>/agents/<name>/HANDOFF.md`; native definition artifacts have kind `agent-definition`, and manual handoffs have kind `agent-handoff`. Unknown and unsupported routes fail closed. The handoff explains the model, reasoning, and permission limitations for the selected definition. Generated definitions do not grant permissions, install an agent, launch a subagent, or enable a production service.

Custom-agent states come from `catalogs/custom-agents.json` and the registry-derived inventory. A native path below is relative to the staged product directory and is used only after manual review/import. Entries without an established route stay unknown; pure provider gateways and Cline's documented instruction-only route are unsupported for this definition export. All custom-agent runtime checks remain pending.

| Product | Surface | Definition state | Native definition path | Model / reasoning fields |
|---|---|---|---|---|
| `abacus` | `desktop` | `unknown` | none | no exported model selector; no exported effort field |
| `aider` | `cli` | `unknown` | none | no exported model selector; no exported effort field |
| `amp` | `cli` | `unknown` | none | no exported model selector; no exported effort field |
| `anthropic` | `api` | `unsupported` | none | no exported model selector; no exported effort field |
| `antigravity` | `ide` | `native` | `.agents/agents/{name}.md` | model `model` (tier); no exported effort field |
| `chatgpt` | `desktop` | `manual` | none | no exported model selector; no exported effort field |
| `chatgpt` | `web` | `manual` | none | no exported model selector; no exported effort field |
| `chatgpt` | `work-local` | `manual` | none | no exported model selector; no exported effort field |
| `chatgpt` | `work-cloud` | `manual` | none | no exported model selector; no exported effort field |
| `claude-code` | `cli` | `native` | `.claude/agents/{name}.md` | model `model` (bare); effort `effort` |
| `cline` | `ide` | `unsupported` | none | no exported model selector; no exported effort field |
| `codex` | `cli` | `native` | `.codex/agents/{name}.toml` | model `model` (bare); effort `model_reasoning_effort` |
| `codex` | `desktop` | `native` | `.codex/agents/{name}.toml` | model `model` (bare); effort `model_reasoning_effort` |
| `codex` | `ide` | `native` | `.codex/agents/{name}.toml` | model `model` (bare); effort `model_reasoning_effort` |
| `copilot` | `vscode` | `native` | `.github/agents/{name}.agent.md` | model `model` (bare); no exported effort field |
| `cursor` | `ide` | `native` | `.cursor/agents/{name}.md` | model `model` (bare); no exported effort field |
| `devin` | `desktop-local` | `unknown` | none | no exported model selector; no exported effort field |
| `factory` | `cli` | `native` | `.factory/droids/{name}.md` | model `model` (bare); effort `reasoningEffort` |
| `google` | `api` | `unsupported` | none | no exported model selector; no exported effort field |
| `goose` | `cli` | `unknown` | none | no exported model selector; no exported effort field |
| `hermes` | `cli` | `manual` | none | no exported model selector; no exported effort field |
| `jetbrains-air` | `desktop` | `manual` | none | no exported model selector; no exported effort field |
| `junie` | `cli` | `unknown` | none | no exported model selector; no exported effort field |
| `kilo` | `cli` | `native` | `.kilo/agents/{name}.md` | model `model` (provider/model); no exported effort field |
| `kilo` | `ide` | `native` | `.kilo/agents/{name}.md` | model `model` (provider/model); no exported effort field |
| `kimi-code` | `cli` | `native` | `.kimi-code/agents/{name}.md` | no exported model selector; no exported effort field |
| `kiro` | `ide` | `native` | `.kiro/agents/{name}.json` | model `model` (bare); no exported effort field |
| `lm-studio` | `desktop` | `unknown` | none | no exported model selector; no exported effort field |
| `lm-studio` | `bionic` | `unknown` | none | no exported model selector; no exported effort field |
| `lm-studio` | `api` | `unknown` | none | no exported model selector; no exported effort field |
| `openai` | `api` | `unsupported` | none | no exported model selector; no exported effort field |
| `opencode` | `cli` | `native` | `.opencode/agents/{name}.md` | model `model` (provider/model); effort `reasoningEffort` |
| `openhands` | `cli` | `unknown` | none | no exported model selector; no exported effort field |
| `openrouter` | `gateway` | `unsupported` | none | no exported model selector; no exported effort field |
| `perplexity` | `app` | `unknown` | none | no exported model selector; no exported effort field |
| `perplexity` | `agent-api` | `unknown` | none | no exported model selector; no exported effort field |
| `perplexity` | `orchestrator` | `unknown` | none | no exported model selector; no exported effort field |
| `pi` | `cli` | `unknown` | none | no exported model selector; no exported effort field |
| `qoder` | `cli` | `native` | `.qoder/agents/{name}.md` | model `model` (bare); effort `effort` |
| `qoder` | `ide` | `native` | `.qoder/agents/{name}.md` | no exported model selector; no exported effort field |
| `qwen-code` | `cli` | `native` | `.qwen/agents/{name}.md` | model `model` (bare); no exported effort field |
| `zed` | `ide` | `manual` | none | no exported model selector; no exported effort field |

Models inherit unless explicitly selected through a supported definition field. Kimi custom agents ignore model selection; Qoder IDE does not receive a guessed model field. Antigravity accepts only its documented `flash`/`pro` tiers. Native effort fields exist only for the recorded Codex, Claude Code, Factory, OpenCode, and Qoder CLI routes; requesting effort requires an explicit model, provider, and matching model capability evidence. No maximum effort is selected automatically. Permission defaults and the limits of each vendor's boundary are recorded in the inventory and HANDOFF; generation cannot change the parent process or vendor trust settings.

Manual custom-agent handoffs reject native model/provider/catalog/effort overrides. Tool filters and prompt instructions do not establish an OS sandbox; the host's installed version, parent policy, and managed settings still apply. No permission or trust is granted by exporting a definition.

## CLI and migration

The CLI provides `inventory`, `coverage`, `models`, `model`, `agents inventory`, `agent`, `render`, `bundle`, `provider`, `doctor`, `report`, and `compare`.

- `render --platform kilo|abacus` is a deprecated alias. It is mutually exclusive with `--agent`, and other integrations require `--agent`.
- Kilo remote HTTP output omits the formerly unsupported `enabled: true` field. Its documented local stdio format includes the enabled marker for an explicitly selected staged entry.
- `--dry-run` prints a JSON envelope with `schema_version: 1` and an `artifacts` list. Each item contains the relative `path`, the `kind`, and UTF-8 `content`. Dry-run validates artifact contents, portable paths and conflicts without writing. Destination ownership and permissions are checked only by an actual write.
- `bundle --skill` is explicit. Skills are never included by default.
- Profile names must be safe identifiers. Custom profiles are loaded only from an explicit `--root`.
- `provider --provider openrouter` preserves the legacy handoff paths and conventions above. A non-OpenRouter provider uses the model setup path and retains explicit model selection.
- Normal wheel and sdist installs read public catalogs, profiles, and skills through `importlib.resources`. There is no checkout or CWD fallback. Editable and source development must pass `--root .` and set `PYTHONPATH=src`.

## Staging ownership, overwrite, and limits

Outputs from `render`, `bundle`, `provider`, `model`, and `agent` are staged vendor configuration, request, or instruction artifacts. Their `--force` option may replace only artifacts recorded as owned in `.agentweaver-manifest.json` whose current SHA-256 still matches the recorded hash. Manually edited or unmanaged files are refused. The manifest records deterministic relative paths, hashes, kinds, and generator and schema identifiers. It excludes secrets and absolute user paths. Entries for untouched outputs are preserved.

For those staged artifacts, symbolic links, junctions and other reparse points, traversal, absolute artifact paths, and conflicting paths are refused. Hash matching is change detection, **not a signature** and not audit evidence of tampering. The writer is not a bundle-wide atomic transaction and is not a cross-process race guarantee.

Benchmark `report` and `compare` output is an ordinary report document, outside the staging manifest. An explicit `--output` writes or replaces that report using normal filesystem semantics. See [benchmark-methodology.md](benchmark-methodology.md) for this separate output contract.

## Security defaults

MCP is default-off. The `enterprise-secure` profile generates zero MCP selections. This applies only to generated artifacts: ambient and global vendor configuration is not inspected, and pre-existing vendor servers are not disabled. Profile retrieval instructions are advisory and are not tool enforcement, a sandbox, a permission grant, or a compliance control.

No vendor entitlement, SSO, or enterprise compliance certification is provided or implied. Native mobile applications, an autonomous agent execution harness, automatic agent installation, live configuration writes, credential discovery, and production-write automation are out of scope.

## Verification status

Native vendor installation and configuration-consumption checks are pending and must be recorded separately with product, surface, version, OS variant, architecture, and observed result. `scripts/verify-platform.py` records local offline checks only and is opt-in; it requires an explicit `--output` path.
