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
- Requested-scope tracking in `catalogs/requested-coverage.json`: 23 requested groups map to 46 distinct registry surfaces. The report shows unknown capabilities, missing documentation, and pending runtime evidence separately. Mapping a requested name does not establish support.
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
agentweaver model --agent qwen-code --provider anthropic --model claude-sonnet-4-6 --reasoning-effort high --dry-run
agentweaver doctor --agent kilo --os windows --format text
```

`--platform kilo|abacus` remains a legacy spelling and is mutually exclusive with `--agent`. Generated files are staging artifacts. Review every artifact and merge it manually; no MCP server is enabled by default.

GitHub's hosted MCP server is available as four opt-in catalog entries: `github` and `github-all` for the default and all toolsets, and `github-readonly` and `github-all-readonly` for GitHub's read-only tool filters. The non-read-only routes can expose write tools. Choose a route deliberately, add it to a reviewed profile allowlist, and explicitly select it when staging; authentication and account permissions are managed separately by the host.

`openai-docs` is an opt-in entry for OpenAI's documentation-only MCP endpoint. It is separate from the removed historical Codex MCP server and does not run Codex or call an inference API.

Aliases such as `OpenAIcodex`, `kiloAI`, `FactoryDroid`, and `LMStudio` resolve through the requested-scope catalog. `VSCodeagents` is a group of named extension surfaces; inspect it with `coverage` and select a concrete product before rendering. JetBrains Air and Junie are separate products; Air desktop and Air in IDEs are distinct `desktop` and `ide` surfaces. ChatGPT Work local/cloud, Perplexity app/API, and LM Studio desktop/Bionic/API are separate surfaces.

Every capability record in the requested scope now has reviewed documentation sources. The report still contains unknown and unsupported states, manual routes, and pending vendor runtime checks. The per-surface OS table in [compatibility.md](docs/compatibility.md#requested-surface-os-workflows) records documented routes and constraints without claiming installed or native-device verification.

`models --provider ID --input snapshot.json` reads a model response captured separately by the operator. It performs no discovery request. `model --agent PRODUCT --provider PROVIDER --model ID` stages model setup; optional reasoning controls are validated for the selected provider, protocol, and documented model or snapshot capabilities. Catalog visibility is not successful inference, installed-version compatibility, or account access. Missing availability, token, or cost data stays unknown.

For LM Studio, a saved native model list retains model types, quantization variants, and loaded-instance context limits separately from each model's maximum context. Native chat checks each requested reasoning value against the model's advertised options, including models that offer only `on`. [LM Studio model-list reference](https://lmstudio.ai/docs/developer/rest/list)

The LM Studio API surface can also stage selections for embedding and decision models from a saved native list. Choose `openai-embeddings`, `openai-decisions`, or `jev-systemone` as the protocol. Decision routes require a loaded decision instance ID from the snapshot. [LM Studio protocol guide](docs/compatibility.md#models-and-reasoning)

`model --agent codex --surface cli --provider lm-studio --protocol openai-responses --model ID` stages a named Codex profile for an already running LM Studio server. The operator reviews and installs the profile separately. [LM Studio's Codex integration](https://lmstudio.ai/docs/integrations/codex)

`model --agent claude-code --surface cli --provider lm-studio --protocol anthropic-messages --model ID` stages a credential-free Claude Code settings file for the local LM Studio Messages endpoint. Review the file before passing it to `claude --settings` and supply authentication separately. [LM Studio's Claude Code integration](https://lmstudio.ai/docs/integrations/claude-code)

`model --agent hermes --surface cli --provider lm-studio --protocol openai-chat --model ID` stages the built-in Hermes LM Studio provider configuration. Review the fragment before merging it into the intended profile. A context override is written only when explicitly supplied. [Hermes LM Studio configuration](https://hermes-agent.nousresearch.com/docs/integrations/providers#lm-studio--desktop-app-with-local-models)

`agent --agent hermes --hermes-profile --name NAME --description TEXT --instructions-file instructions.md` stages a Hermes profile distribution with a manifest and `SOUL.md`. Repeat `--profile-skill ID` to include portable Skills. Add `--mcp-profile efficient --profile-mcp context7` to include a disabled MCP entry in the profile's `config.yaml`. Repeat `--profile-mcp ID` within the selected Weaver profile's allowlist and server limit. To select the profile's model, add `--provider openrouter|lm-studio --model MODEL`. For an OpenRouter model, use `--catalog FILE --reasoning-effort EFFORT` to stage an effort supported by the saved model listing. The default Hermes agent command produces a delegation handoff. [Hermes profile distributions](https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions) · [Hermes MCP settings](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) · [Hermes model settings](https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models)

`--hermes-profile-route` adds a separate parent configuration fragment for Hermes's `advanced-delegation` plugin. It references the generated profile by name. Review the child profile's credentials and terminal access before adding it to a parent's `profile_routes` backend. [Hermes delegation strategies](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation#delegation-strategies-delegationbackend)

Cline CLI has its own `--agent cline --surface cli` route. It stages named agents, portable Skills, disabled MCP server entries for manual merge, and OpenRouter session arguments with model-specific thinking. [Cline config paths](https://docs.cline.bot/getting-started/config) · [Cline CLI flags](https://docs.cline.bot/cli/cli-reference)

Use `--surface ide` for the Cline VS Code extension. The project Skill goes under `.cline/skills`. Review and merge the disabled MCP entries into Cline's user settings, then enable each intended server in the extension. [Cline Skills](https://docs.cline.bot/customization/skills)

Use `--surface desktop` for Cline Desktop. Weaver stages configured agents and portable Skills under `.cline/`, writes disabled MCP entries for manual merge through the app's settings path, and describes how to select the model and thinking level in chat. [Cline Desktop documentation](https://docs.cline.bot/usage/cline-desktop)

`agent --agent copilot --surface vscode --model MODEL --copilot-catalog FILE` stages a custom agent using exact IDs from a saved Copilot SDK `listModels()` array. Add `--copilot-fallback-model ID` for each fallback, `--reasoning-effort EFFORT` when every selected model advertises it, and `--allow-skill ID` for each portable Skill to load. Use `bundle` and `render` to stage Skills and MCP configuration for the same workspace. [VS Code custom agent file reference](https://code.visualstudio.com/docs/agents/reference/custom-agent-file-reference)

`copilot-models --input FILE` inspects that saved catalog offline, including policy state, supported and default reasoning efforts, and token limits. Repeat `--model ID` to check an ordered selection and add `--reasoning-effort EFFORT` to validate it against every selected model. [GitHub Copilot SDK model discovery](https://docs.github.com/en/copilot/how-tos/copilot-sdk/troubleshooting/compatibility)

Add `--copilot-model-policy required` to a catalog-backed custom-agent export to restrict model selection for that agent in the VS Code chat picker. Default behavior permits other available models. [VS Code model preferences](https://code.visualstudio.com/docs/agents/reference/custom-agent-file-reference#_model-preferences)

For the VS Code Copilot harness, add `--copilot-portable` to `render --agent copilot --profile efficient` to stage a workspace-root `.mcp.json`. Explicitly select approved servers with `--enable ID` and review their tool access before merging. [VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)

Use `--agent copilot --surface cli` for standalone GitHub Copilot CLI. Weaver stages project instructions, portable Skills, custom-agent definitions, workspace MCP configuration, and model defaults. For model defaults, pass `--copilot-catalog FILE --model ID` to `model`, with an optional advertised `--reasoning-effort`. [GitHub Copilot CLI configuration](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference)

For external models in standalone Copilot CLI, run `model --agent copilot --surface cli --provider openrouter|lm-studio --model ID`, selecting one provider and adding `--protocol openai-responses` for the Responses route. The handoff includes a credential-free Copilot `providers.json` fragment and the model selector to use after review. Supply `--catalog FILE` with model reasoning evidence before requesting an effort. [GitHub Copilot CLI external models](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-llm-models)

Use `--agent copilot --surface desktop` for a local repository session in the GitHub Copilot app. Weaver stages project Skills, a basic custom-agent profile, and repository MCP configuration. The handoff uses the app's settings and pickers for instructions, external models, and reasoning. [GitHub Copilot app customizations](https://docs.github.com/en/copilot/how-tos/github-copilot-app/customize-github-copilot-app)

Target the Claude Code extension with `--agent claude-code --surface vscode`. The project files use Claude Code's shared paths. The [readiness report](docs/readiness.md) records signed-out checks of the bundled executable on Windows. [Claude Code in VS Code](https://code.claude.com/docs/en/vs-code)

Use `--agent claude-code --surface desktop` for local sessions in Claude Desktop's Code tab. The target stages project instructions, Skills, subagents, MCP files, and catalog-validated model settings. Review existing server names and model defaults before merging. [Claude Code desktop reference](https://code.claude.com/docs/en/desktop)

OpenRouter model snapshots retain each model's advertised endpoints. For embeddings, for example, use `model --agent openrouter --provider openrouter --model ID --catalog FILE --protocol openrouter-embeddings` to stage a selector for a model that advertises the embeddings route. The compatibility guide lists the other supported task routes. [OpenRouter model-list endpoint reference](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)

OpenRouter inventory also includes reported alias targets, voice IDs, and top-provider limits. With a saved model listing, the Chat Completions, Responses, and Messages selectors can stage an advertised reasoning effort, budget, or toggle. [OpenRouter reasoning controls](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)

To stage the model and request endpoint for OpenRouter Batch, use `--protocol openrouter-batch --batch-endpoint /v1/responses` with a saved catalog containing the model's batch variant. Add the request bodies separately. [OpenRouter Batch API quickstart](https://openrouter.ai/docs/batch-quickstart)

`computer-models --input snapshot.json` separately inspects a caller-saved Perplexity Computer MCP `models_list` response. Add `--mode NAME` or `--model ID` and optional `--reasoning-effort VALUE` to check an explicit choice against that snapshot. It does not connect, authenticate or start a task; captured account choices may change and do not prove current access or inference.

`kimi-models --input snapshot.json` separately inspects a caller-saved successful Kimi web `GET /api/v1/models` response. Its model identifiers are configured aliases, including aliases with spaces, not necessarily upstream provider model IDs. Optional `--model ALIAS --reasoning-effort VALUE` checks the alias and its supported thinking effort without changing Kimi settings. The snapshot does not establish current provider access or successful inference.

A provider-reported context length of zero stays visible in inventory as the reported value and produces an unknown usable context size. It does not configure a zero-length context window. Explicit `--context-size` values must be positive; Kimi native model configuration still requires an established positive limit.

For a separately captured OpenRouter inventory across model types, use its documented `output_modalities=all` filter; the endpoint otherwise defaults to text output. Imported conditional prices remain conditional, unresolved prices remain unknown, and modality metadata helps distinguish task-specific models from language-agent model choices. See the [model snapshot contract](docs/compatibility.md#models-and-reasoning).

`models --agent PRODUCT --surface SURFACE` inspects that host's documented model chooser and reasoning controls. This host inventory is separate from importing a provider snapshot with `--input`.

Kiro CLI V3 uses `--agent kiro --surface cli` for workspace Steering, Skills, disabled MCP entries, and read-only custom agents. Use `model --agent kiro --surface cli --model MODEL --reasoning-effort EFFORT` to stage launch arguments for one session. Add `--kiro-model-defaults` to stage a per-model settings fragment for manual merge. Use a Kiro model ID and confirm the choices in `/model`. [Kiro configuration scopes](https://kiro.dev/docs/configuration/) · [Kiro reasoning effort](https://kiro.dev/docs/models/effort/)

Use `model --agent qoder --surface cli --model MODEL` to stage Qoder launch arguments. Add `--reasoning-effort EFFORT` and `--context-size TOKENS` as supported by the selected model. Add `--qoder-settings` to stage a JSON fragment for `qoder --settings` or a reviewed merge into existing Qoder settings. Use the exact ID from the target account and confirm its options in `/model`. [Qoder model selection](https://docs.qoder.com/cli/model) · [Qoder settings reference](https://docs.qoder.com/cli/settings-reference)

`qoder-models --input FILE` inspects a saved Qoder Agent SDK `getAvailableModels()` JSON array. Add `--qoder-catalog FILE` to `model --agent qoder` or `agent --agent qoder` to verify an explicit CLI model and any supplied options against the snapshot before staging. [Qoder TypeScript SDK model metadata](https://docs.qoder.com/cli/sdk/references-typescript)

For Zed, `model --agent zed --surface ide --provider openrouter --model MODEL` stages `agent.default_model` for manual merge into Zed settings. Add `--zed-subagent` to stage the shared `agent.subagent_model` instead, and `--catalog PATH --reasoning-effort EFFORT` to validate an explicit effort against a saved OpenRouter catalog. Confirm the available values in Zed's native model picker. [Zed Agent settings](https://zed.dev/docs/ai/agent-settings#subagent-models)

The host-control inventory covers all 55 registered surfaces and preserves unknown availability. JetBrains Air desktop's shared reasoning override remains unknown. Air in IDEs separately documents agent-dependent model and effort controls through manual presets and session settings. OpenHands CLI has model-dependent effort and thinking-budget controls through its pinned SDK configuration; those controls remain separate from custom-agent fields. A documented effort, budget, toggle, variant, or mode remains specific to the selected host and model. The inventory does not enable additional native exporters or prove access to a listed model.

For `--agent jetbrains-air --surface ide`, bundles stage advisory instructions and `<agent>/read/<name>.md` context files. Apply instructions through a preset's Advanced > Injected instructions; Add context > Skills or `$` selects Skills already exposed by the host, not these staged files. Manual custom-agent output describes a UI preset, without inventing an agent file, Skill import loader, OpenRouter configuration or IDE OS matrix. The Air desktop route keeps its separate contract.

`agents inventory` reports custom-agent definition support separately from project instructions and Skills. `agent --agent PRODUCT --name NAME --description TEXT --instructions-file instructions.md` stages a documented definition or a manual handoff. Optional model and reasoning fields follow that definition format's restrictions. No definition is installed or launched, and no permissions are granted by generating it.

For Qwen Code CLI subagents, pass `--qwen-auth-type TYPE --model MODEL` to stage an explicit `TYPE:MODEL` selector for a built-in Qwen authentication type. The target profile supplies the model entry, credentials, and reasoning settings. [Qwen Code subagents](https://qwenlm.github.io/qwen-code-docs/en/users/features/sub-agents/)

Use `qwen-models --input FILE` to inspect a saved `GET /workspace/providers` response from Qwen Code's daemon. Optional `--auth-type`, `--model`, and `--reasoning-effort` check an advertised workspace route and option offline. [Qwen Code daemon mode](https://qwenlm.github.io/qwen-code-docs/en/users/qwen-serve/)

`model --agent qwen-code --qwen-workspace-catalog FILE --model ROUTE` stages a daemon session startup body for an exact advertised model route. Add `--qwen-auth-type TYPE` to filter the snapshot and `--reasoning-effort OPTION` to request an advertised option. [Qwen daemon startup protocol](https://qwenlm.github.io/qwen-code-docs/en/developers/qwen-serve-protocol/)

`model --agent kimi-code --kimi-catalog FILE --kimi-workspace-id ID --model ALIAS` stages a local Kimi web session creation body from a saved model list. Add `--reasoning-effort EFFORT` to choose an advertised thinking level and `--kimi-agent-profile NAME` to reference an existing main-agent definition. [Kimi web session endpoints](https://moonshotai.github.io/kimi-code/en/reference/server-api-sessions.html)

`model --agent kimi-code --kimi-catalog FILE --kimi-subagent-pool --model ALIAS` stages a subagent model default. Repeat `--kimi-subagent-alias ALIAS` to offer more configured models. Use `--reasoning-effort EFFORT` when every selected alias advertises the same desired effort. [Kimi subagent model pool](https://moonshotai.github.io/kimi-code/en/configuration/config-files.html#secondary_model)

Use `codex-models --input saved-model-list.json` to inspect model IDs, picker visibility, supported reasoning efforts and defaults from saved Codex app-server replies. Supply a single reply or a JSON object with a `pages` array of replies in capture order. Use `--model MODEL --reasoning-effort EFFORT` to check a specific choice offline. [Codex app-server model/list](https://learn.chatgpt.com/docs/app-server#list-models-modellist)

Use `claude-models --input saved-model-info.json` to inspect a saved Claude Code `ModelInfo[]` list. Pass `--claude-catalog FILE --model SELECTOR` to `model` or `agent` for the Claude Code CLI, VS Code extension, or local Desktop Code tab. An explicit effort must be advertised by the selected entry. [Claude Agent SDK model metadata](https://code.claude.com/docs/en/agent-sdk/typescript#modelinfo)

Use `factory-models --input saved-droid-models.json` to inspect a saved Factory TypeScript SDK `listModels()` array. Pass `--factory-catalog saved-droid-models.json --model MODEL` to `model --agent factory` to stage `droid exec` arguments for a managed or configured custom model, or to `agent --agent factory` to validate a custom-droid model pin. Preserve the exact catalog ID and refresh the snapshot if the custom-model configuration or order changes. An explicit `--reasoning-effort` must be supported by both the selected model and the target format. [Factory SDK model discovery](https://docs.factory.com/sdk/typescript#discover-available-models)

For a Codex custom agent, add `--model MODEL --codex-catalog saved-model-list.json --reasoning-effort EFFORT` to stage a choice validated against the same snapshot. Codex-specific effort labels such as `ultra` are accepted when the selected visible model advertises them. Omit the effort to leave that field unset. Saved catalog metadata describes the client at capture time. Account access and inference remain unverified.

For an OpenCode V2 custom agent, use `opencode-models --input raw-models.json` to emit a selector catalog from a saved V2 `GET /api/model` response. Pass that catalog to `agent --agent opencode --model PROVIDER/MODEL --opencode-catalog selectors.json` and add `--model-variant VARIANT` when needed. Weaver validates the exact variant against the selected enabled chat model and stages its selector and permissions. Existing OpenCode agent exports keep their V1 format. [OpenCode compatibility](docs/compatibility.md#custom-agent-definitions)

For a Kilo CLI or VS Code custom agent, use `kilo-models --input raw-kilo-models.json` to extract selectors from a saved Kilo `GET /api/model` response. Pass the resulting JSON to the agent exporter with `--kilo-catalog selectors.json --model PROVIDER/MODEL`. Add `--model-variant VARIANT` to select an advertised variant for an enabled chat model with text output. The generated Markdown stores `model` and `variant` in separate fields. Omit the variant to leave Kilo's default selection in effect. [Kilo custom agents](https://kilo.ai/docs/customize/custom-modes)

Repeat `--allow-skill ID` on the V2 agent command for each portable Skill the agent may load. Stage matching files with `bundle --agent opencode --profile efficient --skill ID`. OpenCode V2 discovers the bundle's `.agents/skills` path and resolves the active definition for each ID through its normal precedence rules. [OpenCode Skills](https://opencode.ai/docs/skills/)

`render --agent opencode --profile efficient --opencode-v2 --enable context7` stages the selected server under `mcp.servers` in `opencode/opencode.json` with `disabled: true`. Review and merge the entry before explicitly enabling it in OpenCode. The same format supports local stdio entries from an allowlisted catalog. [OpenCode MCP configuration](https://opencode.ai/docs/mcp-servers/#config)

Cursor CLI uses `--agent cursor --surface cli` for staged MCP entries, project instructions, Skills, and custom subagents. The CLI lists its account models with `agent --list-models` after sign-in. Use `model --agent cursor --surface cli --model MODEL` to stage a session selector. [Cursor CLI reference](https://cursor.com/docs/cli/reference/parameters)

For Cursor CLI and IDE subagents, use `cursor-models --input PATH` to inspect a saved Cursor SDK model list. Pass it with `agent --cursor-catalog PATH` to validate and stage model-specific `--reasoning-effort` and `--cursor-param ID=VALUE` options. [Cursor subagent selectors](docs/compatibility.md#custom-agent-definitions)

Agent Canvas uses `--agent openhands --surface canvas` for staged repository instructions and Skills, disabled per-server MCP request bodies, and manual agent and LLM profile setup. See the [Canvas MCP workflow](docs/compatibility.md#mcp-transports-and-stdio-staging) and [profile setup](docs/compatibility.md#custom-agent-definitions).

The OpenHands CLI route is scoped to audited CLI 1.16.0 with SDK 1.28.1: it stages `.agents/agents/<name>.md` with `model: inherit` by default and `tools: []`. Use `agent --agent openhands ... --llm-profile NAME` to reference an existing named local LLM profile instead. Weaver records that reference without reading the profile, checking its existence, or creating it. A profile reference cannot be combined with raw `--model`, provider, catalog, or effort overrides; profile availability, compatibility, and inference remain unknown. Its user MCP fragment supports remote URLs and structured stdio for manual import; model/provider setup remains a CLI Settings handoff. See the [OpenHands boundaries](docs/compatibility.md#custom-agent-definitions).

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
