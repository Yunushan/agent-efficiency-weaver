"""Offline requested-scope mapping, separate from capability and runtime support."""
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .config import WeaverError, load_json
from .integrations import (
    CAPABILITY_NAMES,
    OS_IDS,
    IntegrationSpec,
    _canonical_id,
    _strict_object,
    _string,
    _string_list,
    load_integrations,
)

SCHEMA_VERSION = 1
LIMITATIONS = (
    "Requested-scope mapping records which product surfaces were considered; it does not establish support.",
    "Documentation evidence, fixture tests and recorded runtime checks are separate.",
    "Unknown capabilities and missing evidence remain unresolved; a mapping is not a native exporter.",
    "Model IDs and reasoning controls depend on the selected product, surface, provider and model.",
    "Recorded observations apply to the stated version, execution surface, environment and check.",
    "Coverage does not run clients, check account entitlement or verify exhaustive model availability.",
)

_OBSERVATION_CHECKS = {
    "perplexity-public-plugin-skill-preview": (
        "skills", "The signed-out browser displayed a published plugin's Skill and connector "
        "sections. The probe viewed public catalog metadata.",
    ),
    "perplexity-public-model-picker-sign-in": (
        "models", "Clicking Model in signed-out Search displayed a sign-in prompt. The signed-out "
        "Computer page offered sign-in without exposing the account's model or effort choices.",
    ),
    "perplexity-agent-catalog-authentication": (
        "models", "GET /v1/models without credentials returned HTTP 401 with a JSON error. "
        "Project model visibility remains unverified.",
    ),
    "perplexity-orchestrator-catalog-authentication": (
        "models", "GET /orchestrator/v1/models without credentials returned HTTP 401 with a JSON error. "
        "Private-preview catalog access remains unverified.",
    ),
    "perplexity-orchestrator-model-lookup-authentication": (
        "models", "Orchestrator model lookups returned HTTP 401 without credentials for both literal and "
        "percent-encoded slashes in the full model ID. Authenticated path handling remains unverified.",
    ),
    "perplexity-orchestrator-openai-mcp-round-trip": (
        "mcp", "The OpenAI Python SDK serialized selected tools and their results for Chat Completions and Responses. "
        "The probe called a local stdio MCP fixture and returned its result with the original call ID using mocked "
        "provider transport. Hosted access and tool acceptance remain unverified.",
    ),
    "perplexity-orchestrator-anthropic-mcp-round-trip": (
        "mcp", "The Anthropic Python SDK serialized selected tools and their results for Messages. "
        "The probe called a local stdio MCP fixture and returned its result with the original tool-use ID using mocked "
        "provider transport. Hosted access and tool acceptance remain unverified.",
    ),
    "perplexity-orchestrator-openai-skill-round-trip": (
        "skills", "The OpenAI Python SDK sent the selected Skill index through Chat Completions and Responses. "
        "With mocked provider transport, the test client returned one Skill body on demand with the original call ID. "
        "Hosted access and model use of the Skill remain unverified.",
    ),
    "perplexity-orchestrator-anthropic-skill-round-trip": (
        "skills", "The Anthropic Python SDK sent the selected Skill index through Messages. "
        "With mocked provider transport, the test client returned one Skill body on demand with the original tool-use ID. "
        "Hosted access and model use of the Skill remain unverified.",
    ),
    "openrouter-public-model-catalog": (
        "models", "The public v1 model-list API returned text, all-modality and reasoning-filtered lists without credentials. "
        "Three offset pages matched the ordered model IDs, endpoints and reasoning metadata in the unpaginated all-modality response.",
    ),
    "openrouter-public-model-route-validation": (
        "models", "Offline validation accepted the captured model IDs for their advertised endpoints. "
        "This check did not invoke those endpoints.",
    ),
    "openrouter-public-provider-endpoint-inventory": (
        "models", "Unauthenticated model-specific requests returned provider tags and advertised parameters "
        "for the sampled catalog entries. Account access, request routing, and inference remain unverified.",
    ),
    "openrouter-public-reasoning-payload-validation": (
        "reasoning", "Offline validation projected catalog-advertised reasoning controls into Chat Completions, "
        "Responses and Messages payloads. No inference requests were sent.",
    ),
    "antigravity-desktop-workspace-instructions": (
        "instructions", "The bundled language server read the generated workspace AGENTS.md. "
        "Withholding the file removed it from the inventory.",
    ),
    "antigravity-desktop-workspace-skill": (
        "skills", "The bundled language server found the generated workspace Skill and returned its instructions. "
        "Withholding the file removed it from the inventory.",
    ),
    "antigravity-desktop-workspace-agent": (
        "agents", "The bundled language server listed the generated custom subagent as an enabled workspace definition. "
        "Withholding the file removed the entry.",
    ),
    "antigravity-desktop-mcp-inventory": (
        "mcp", "The bundled language server recognized generated HTTPS and stdio entries in separate workspace "
        "inventories. Withholding the project file removed the entry. The inventory did not exercise tool calls.",
    ),
    "antigravity-desktop-signed-out-model-boundary": (
        "models", "The isolated bundled language server reported no signed-in account and returned no model "
        "entries or model reasoning configuration.",
    ),
    "antigravity-desktop-mcp-user-stdio-prompt": (
        "mcp", "The bundled language server loaded the generated fragment from a disposable user configuration, "
        "listed the stdio fixture tool and retrieved the fixture prompt. Removing the file removed the server.",
    ),
    "antigravity-desktop-mcp-user-https-discovery": (
        "mcp", "The bundled language server reached OpenAI Docs over Streamable HTTPS from the exact user fragment "
        "and listed its tools. Removing the file removed the server.",
    ),
    "antigravity-desktop-mcp-workspace-refresh-boundary": (
        "mcp", "Tracking a disposable project and refreshing MCP through the signed-out language server's "
        "MCP manager did not launch the fixture when it was configured only at workspace scope.",
    ),
    "antigravity-ide-workspace-instructions": (
        "instructions", "The bundled IDE language server returned the generated workspace AGENTS.md. Its absence removed it from inventory.",
    ),
    "antigravity-ide-workspace-skill": (
        "skills", "The bundled IDE language server returned the generated Skill from its exact workspace path. Its absence removed it from inventory.",
    ),
    "antigravity-ide-mcp-user-stdio-discovery": (
        "mcp", "The bundled IDE language server initialized the local stdio fixture from the generated user fragment and listed its tool. "
        "The fresh workspace-only and empty controls did not start the fixture.",
    ),
    "antigravity-ide-mcp-user-https-discovery": (
        "mcp", "The bundled IDE language server listed five public OpenAI Docs tools over Streamable HTTPS from the "
        "generated user fragment. A fresh user profile without the fragment did not list the server.",
    ),
    "antigravity-ide-mcp-user-stdio-prompt": (
        "mcp", "The bundled IDE language server retrieved a synthetic prompt from a local stdio fixture using the "
        "generated user fragment. The server and prompt were absent in the control. No model request ran.",
    ),
    "antigravity-ide-signed-out-model-inventory": (
        "models", "The isolated IDE language server returned empty model and configuration inventories without an account token.",
    ),
    "antigravity-cli-account-catalog-authentication": (
        "models", "The signed-out account model listing requested sign-in. The Gemini-compatible mode listed "
        "its bundled selectors with a loopback endpoint and a synthetic key.",
    ),
    "antigravity-sdk-instructions-config": (
        "instructions", "The released Python SDK constructor accepted the staged prompt through system_instructions.",
    ),
    "antigravity-sdk-skill-path-config": (
        "skills", "The SDK configuration kept explicit root and child Skill paths. The staged Skill files existed.",
    ),
    "antigravity-sdk-subagent-config": (
        "agents", "The SDK SubagentConfig constructor accepted generated instructions and both explicit and inherited models.",
    ),
    "antigravity-sdk-mcp-config": (
        "mcp", "The released Python types accepted both transports, exact tool allowlists and separate stdio arguments. "
        "No server connection or tool call ran.",
    ),
    "antigravity-sdk-model-config": (
        "models", "The SDK accepted the staged model selector in Gemini, Vertex AI, Ollama and LM Studio configurations.",
    ),
    "antigravity-sdk-thinking-config": (
        "reasoning", "The SDK parsed all five explicit Gemini and Vertex thinking levels and their omitted defaults. "
        "The probe stopped at configuration construction.",
    ),
    "antigravity-cli-workspace-instructions": (
        "instructions", "The headless runtime included the generated AGENTS.md in its loopback request.",
    ),
    "antigravity-cli-skill-expansion": (
        "skills", "Headless slash invocation expanded the generated Skill into the loopback request. "
        "Withholding the Skill file prevented expansion.",
    ),
    "antigravity-cli-subagent-tier": (
        "agents", "A delegated custom subagent used its generated instructions and the Flash tier.",
    ),
    "antigravity-cli-subagent-workspace-skill-read": (
        "skills", "The generated reviewer read the entire staged Skill through read_file after seeing its ID "
        "in the initial request. Removing the Skill file returned a missing-file result. Omitting the "
        "reference still allowed reading the staged Skill, and a second workspace Skill absent from the "
        "explicit list was also visible and readable.",
    ),
    "antigravity-cli-scoped-stdio-call": (
        "mcp", "The trusted workspace fixture initialized and listed tools. The headless call was denied "
        "until the isolated profile allowed the exact server and tool. The call returned the fixture result.",
    ),
    "antigravity-cli-subagent-mcp-isolation": (
        "mcp", "The generated subagent suppressed inherited workspace MCP tools visible to its parent.",
    ),
    "antigravity-cli-model-selector-request": (
        "models", "Generated CLI arguments selected Gemini 3.1 Pro and Gemini 3.8 Flash in separate loopback requests.",
    ),
    "antigravity-cli-effort-request": (
        "reasoning", "The loopback endpoint observed different thinking budgets for Gemini 3.1 Pro low and high, "
        "and the thinking level for Gemini 3.8 Flash medium. The CLI rejected a conflicting effort and slug.",
    ),
    "antigravity-cli-bundled-variant-requests": (
        "reasoning", "All 21 selectors advertised by the Gemini-compatible CLI mode completed a scripted "
        "loopback turn. The endpoint recorded the resolved model and thinking settings for each selector.",
    ),
    "antigravity-cli-custom-model-request": (
        "models", "The generated settings added an alias visible to agy models. Generated launch arguments "
        "sent the exact model path to a scripted loopback endpoint. Without the entry the alias was absent. "
        "The documented host-bearing form added the host to the request path on this build.",
    ),
    "antigravity-cli-custom-model-thinking": (
        "reasoning", "Generated settings and arguments sent LOW, MEDIUM, and HIGH thinking levels. "
        "Turning thinking off sent budget zero, and omitting the effort used budget -1. "
        "The CLI rejected xhigh, max, and explicit effort with thinking disabled before any request.",
    ),
    "antigravity-cli-gemini-provider-staging": (
        "provider", "The headless CLI read the generated Gemini provider fragment and returned the response "
        "from a scripted Gemini-compatible endpoint. With the process key omitted, it stopped before making a request.",
    ),
    "antigravity-cli-gemini-built-in-reasoning": (
        "reasoning", "The staged Gemini 3.8 Flash medium selector sent thinkingLevel MEDIUM. The staged Gemini "
        "3.1 Pro high selector resolved to gemini-3.1-pro-preview and sent thinkingBudget -1 on the tested build.",
    ),
    "antigravity-cli-adc-credential-boundary": (
        "provider", "The headless CLI selected the ADC credential loader with the staged environment and rejected "
        "a missing credential file and an invalid credential type. Both stopped at authentication. "
        "Authorized model access and reasoning remain unverified.",
    ),
    "antigravity-cli-project-context7-https-call": (
        "mcp", "The headless CLI loaded the exact generated workspace entry, called the public Context7 "
        "resolver over Streamable HTTPS and returned the React library ID.",
    ),
    "antigravity-cli-project-openai-docs-https-call": (
        "mcp", "The headless CLI loaded the exact generated workspace entry and returned a public "
        "documentation link from OpenAI Docs over Streamable HTTPS.",
    ),
    "antigravity-cli-project-https-permissions": (
        "mcp", "The Context7 call required an exact scoped permission in the isolated home. No grant "
        "or a grant for a different tool denied the call. The empty project config exposed no MCP tool.",
    ),
    "antigravity-cli-acp-model-inventory": (
        "models", "The ACP server required authentication for a new session. Its Gemini-compatible mode "
        "listed 21 bundled model variants after using a synthetic key and loopback endpoint.",
    ),
    "antigravity-cli-acp-variant-requests": (
        "reasoning", "ACP session configuration selected each bundled model and thinking level. "
        "All 21 variants sent their resolved model and thinking settings to a scripted loopback "
        "endpoint. Hydrogen rejected the unavailable max setting.",
    ),
    "cursor-cli-mcp-approval-and-tool-discovery": (
        "mcp", "The signed-out CLI read the exact generated MCP file, started the stdio fixture only "
        "after individual approval, and listed its tool. Disabling the server stopped launches, and "
        "withholding the file removed the entry.",
    ),
    "cursor-cli-context7-https-inventory": (
        "mcp", "The signed-out CLI discovered both Context7 tools over Streamable HTTPS after approving "
        "the exact generated project entry. Disabling the server or withholding the config removed the tools.",
    ),
    "cursor-cli-catalog-authentication": (
        "models", "The isolated signed-out CLI required authentication before listing account models.",
    ),
    "cursor-cli-bundled-subagent-load": (
        "agents", "The CLI package's bundled loader read the exact generated name, prompt, readonly mode, and "
        "parameterized model string. Withholding the file removed the agent. The loader was invoked directly.",
    ),
    "cursor-cli-bundled-preset-selector-load": (
        "reasoning", "The bundled subagent loader preserved all model, effort, speed and context parameters "
        "in three generated selectors from synthetic presets. Withholding the files removed the entries.",
    ),
    "cursor-cli-bundled-skill-load": (
        "skills", "The CLI package's bundled loader read the exact generated Skill. Withholding the Skill file "
        "removed it from discovery. The loader was invoked directly.",
    ),
    "cursor-ide-bundled-instruction-load": (
        "instructions", "The IDE's bundled project rules loader read the generated AGENTS.md. "
        "Withholding the file removed it from the rule inventory.",
    ),
    "cursor-ide-bundled-skill-load": (
        "skills", "The IDE's bundled Skills loader returned the exact generated Skill content. "
        "Withholding the file removed it from discovery.",
    ),
    "cursor-ide-bundled-subagent-load": (
        "agents", "The IDE's bundled loader read the generated subagent names, descriptions, prompts and "
        "read-only modes. Withholding the agent files removed them. The same loader kept the literal "
        "quotes in older generated names and model selectors.",
    ),
    "cursor-ide-bundled-selector-load": (
        "reasoning", "The IDE's bundled subagent loader preserved the inherited selector and a synthetic "
        "model selector with explicit effort and fast parameters.",
    ),
    "cursor-ide-bundled-mcp-inventory": (
        "mcp", "The IDE's bundled loader inventoried the generated Context7 HTTPS and stdio entries, "
        "both requiring approval. The default generated config listed no servers.",
    ),
    "cursor-ide-bundled-mcp-stdio": (
        "mcp", "The IDE's bundled MCP client called a read-only fixture from the generated stdio entry "
        "after project approval. The fixture stayed idle before approval and after revocation. "
        "Withholding the project config removed the server.",
    ),
    "skill-discovery": ("skills", "The client listed the enabled generated project Skill."),
    "codex-desktop-chatgpt-plugin-skill-inventory": (
        "skills", "The signed-out bundled App Server read all six selected Skills in the generated ChatGPT plugin. "
        "Removing the marketplace file removed the package from the local inventory.",
    ),
    "codex-desktop-chatgpt-plugin-mcp-inventory": (
        "mcp", "The bundled App Server read the selected HTTPS and stdio entry names from the generated package. "
        "The skills-only package had no MCP entries. The packages stayed uninstalled and the stdio fixture stayed idle.",
    ),
    "codex-desktop-chatgpt-named-plugin-skills": (
        "skills", "The bundled App Server discovered two generated plugin names in the same marketplace. "
        "The shared Skill appeared under each plugin's namespace. Withholding an entry removed only that plugin.",
    ),
    "codex-desktop-chatgpt-named-plugin-mcp": (
        "mcp", "The bundled App Server returned the correct selected HTTPS entry for each generated plugin. "
        "Both packages stayed uninstalled and no MCP tool was called.",
    ),
    "chatgpt-local-plugin-skill-inventory": (
        "skills", "The signed-out Desktop-bundled App Server listed all six namespaced Skills in the staged "
        "package. Withholding one Skill removed only that entry. Withholding the marketplace hid the package. "
        "This was a package discovery check without GUI use or activation.",
    ),
    "chatgpt-local-plugin-mcp-inventory": (
        "mcp", "The signed-out Desktop-bundled App Server listed the selected HTTPS and stdio server names. "
        "Both the Skills-only export and the withheld MCP file returned an empty MCP list. "
        "The package stayed uninstalled and disabled, and the stdio fixture never started.",
    ),
    "chatgpt-web-public-plugin-mcp": (
        "mcp", "The signed-out web Plugins directory showed a published custom plugin's MCP server declaration "
        "and an installation link requiring sign-in. No installation or tool call was exercised.",
    ),
    "chatgpt-web-chat-model-picker": (
        "models", "An existing signed-in Chat session displayed GPT-6 and GPT-5.6 Sol in Select model. "
        "The observation records labels from one account without selecting a model or starting a turn.",
    ),
    "chatgpt-web-chat-reasoning-control": (
        "reasoning", "The signed-in Chat composer reported Extra High at position four of five. "
        "The probe read the current setting without changing it or running inference.",
    ),
    "chatgpt-web-skill-authoring-menu": (
        "skills", "The signed-in Skills screen offered Create with chat, Create with editor, "
        "and Upload from your computer. The probe opened the menu without adding a Skill.",
    ),
    "chatgpt-work-cloud-model-picker": (
        "models", "The signed-in Work web composer listed Default and eight named model options. "
        "No Work task was started and the catalog is scoped to the observed account.",
    ),
    "chatgpt-work-cloud-reasoning-control": (
        "reasoning", "The signed-in Work web composer showed Light at position two of six and a Fast mode control. "
        "Neither control was changed and no Work task was started.",
    ),
    "chatgpt-work-cloud-plugin-picker": (
        "mcp", "The Work web composer opened its searchable plugin picker. "
        "The check covered the picker without inspecting a server connection or invoking a tool.",
    ),
    "agent-discovery": ("agents", "The runtime discovered the generated project agent and loaded its prompt."),
    "devin-desktop-project-instructions-inventory": (
        "instructions", "The bundled ACP engine listed the generated AGENTS.md as always-on. "
        "The rule disappeared when the standard instruction import was disabled or the file was withheld.",
    ),
    "devin-desktop-project-skill-inventory": (
        "skills", "The bundled ACP engine listed all six generated workspace Skills. "
        "The empty project control listed no workspace Skills.",
    ),
    "devin-desktop-project-subagent-inventory": (
        "agents", "The bundled ACP engine listed the generated workspace subagent and its declared sonnet model alias. "
        "The empty project control listed no custom subagent.",
    ),
    "devin-desktop-project-mcp-config-inventory": (
        "mcp", "The bundled engine's CLI read the generated stdio and HTTPS entries with their disabled state "
        "and disabled tool names. The empty project control listed neither entry.",
    ),
    "devin-desktop-project-stdio-handshake": (
        "mcp", "The bundled ACP engine sent discovery and completed initialization with the selected local stdio fixture. "
        "The fixture received no messages when the generated entry was disabled.",
    ),
    "agent-load": ("agents", "A spawned custom agent received its generated developer instructions."),
    "stdio-tool-call": ("mcp", "The parent session called a local stdio fixture from the generated configuration."),
    "stdio-subagent-approved-tool-call": ("mcp", "The subagent called the fixture with explicit local tool approval."),
    "stdio-subagent-tool-approval": (
        "mcp", "The subagent discovered the fixture from the generated file. The call required approval, and a scoped "
        "approval in the isolated home allowed it without editing the project file.",
    ),
    "remote-config-parse": ("mcp", "The client parsed the remote MCP entry without opening a connection."),
    "public-docs-https-tool-call": (
        "mcp", "The signed-out client discovered the public OpenAI Docs tools and called the documentation list directly.",
    ),
    "public-docs-https-subagent-tool-call": (
        "mcp", "A generated subagent inherited the project's public OpenAI Docs server and called its documentation list "
        "over HTTPS without an approval override. The signed-out probe used the host TLS root and scripted model responses.",
    ),
    "context7-https-subagent-tool-call": (
        "mcp", "A generated subagent inherited the project's Context7 server and resolved a public library over HTTPS. "
        "The signed-out probe supplied the host TLS root to the isolated process and scripted model responses locally.",
    ),
    "workspace-mcp-parse": ("mcp", "The native workspace parser accepted generated HTTP and stdio entries."),
    "workspace-stdio-tool-call": (
        "mcp", "The bundled runtime loaded the trusted workspace file, discovered the local tool, and called it directly.",
    ),
    "copilot-vscode-agent-mcp-tool-selection": (
        "mcp", "The bundled runtime used the generated agent's exact tool reference in a loopback model turn. "
        "It called the selected stdio tool and returned its result. A second tool on the same server stayed out "
        "of the agent's request. Withholding the reference or workspace configuration hid the selected tool.",
    ),
    "copilot-desktop-bundled-agent": (
        "agents", "The Desktop-bundled engine loaded the explicitly selected repository agent and its "
        "instructions. Withholding the staged agent file prevented selection.",
    ),
    "copilot-desktop-bundled-skills": (
        "skills", "The Desktop-bundled engine listed all six staged Skills. A scripted invocation returned "
        "the selected Skill's instructions, and withholding the Skills removed them from the inventory.",
    ),
    "copilot-desktop-bundled-mcp": (
        "mcp", "The engine started the fixture from the generated repository file and returned its tool result. "
        "Empty, missing and untrusted MCP configurations left the fixture idle.",
    ),
    "copilot-desktop-bundled-mcp-permissions": (
        "mcp", "The generated agent's tool aliases hid the fixture tools after discovery. In a plain "
        "noninteractive session, an unapproved unannotated tool and an explicitly denied read-only tool "
        "received no call. The read-only tool ran with no explicit approval when no denial applied.",
    ),
    "copilot-desktop-bundled-model": (
        "models", "The model selected in the bundled engine resolved to the fixture model ID in a scripted "
        "Responses request. This exercised the engine's local BYOK configuration.",
    ),
    "copilot-desktop-bundled-effort": (
        "reasoning", "The bundled engine sent low, medium and high from its launch flags to the local "
        "Responses endpoint.",
    ),
    "copilot-cli-skill-use": (
        "skills", "The CLI invoked the generated project Skill and returned its instructions to a scripted local endpoint.",
    ),
    "copilot-cli-byok-model-request": (
        "models", "The generated BYOK selector mapped to the exact upstream model ID in a loopback Responses request.",
    ),
    "copilot-cli-byok-effort-request": (
        "reasoning", "The local Responses endpoint observed both high and medium from the CLI launch flag.",
    ),
    "copilot-cli-child-model-request": (
        "models", "The delegated custom agent used its own configured model in the scripted loopback session.",
    ),
    "copilot-cli-child-model-fallback": (
        "models", "The delegated custom agent used the first available model in the generated ordered model field.",
    ),
    "copilot-cli-child-effort-request": (
        "reasoning", "The generated agent's reasoning-effort set the child to high while the parent used low.",
    ),
    "copilot-cli-child-mcp-filter": (
        "mcp", "The generated agent's strict tool list excluded the workspace fixture visible to the parent.",
    ),
    "model-catalog": ("models", "The client listed model IDs and advertised reasoning efforts across its catalog pages."),
    "agent-model-effort-selection": ("reasoning", "The selected child model and effort reached a scripted local endpoint."),
    "codex-agent-model-effort-precedence": (
        "reasoning", "The generated agent's model and effort reached the scripted local endpoint despite conflicting spawn settings."
    ),
    "codex-agent-model-only-preserves-spawn-effort": (
        "reasoning", "The generated agent set the child model to gpt-6-luna and preserved medium effort from the spawn "
        "request when the agent file omitted effort. The child also received the project's read-only stdio tool result.",
    ),
    "codex-picker-pair-request-matrix": (
        "models", "The generated agent sent all 44 advertised ordinary model and effort pairs from the signed-out "
        "picker to the scripted Responses endpoint without changing either value.",
    ),
    "codex-picker-ultra-request-resolution": (
        "reasoning", "The generated agent selected all six picker-visible models that advertised ultra. The tested "
        "engine sent xhigh for three models and max for three in ordinary Responses requests.",
    ),
    "codex-agent-inherited-read-only-mcp": (
        "mcp", "The generated agent called a read-only stdio tool from the trusted project's MCP configuration without a tool approval override."
    ),
    "codex-agent-project-mcp-precedence": (
        "mcp", "The trusted project's MCP server handled the read-only call. A same-name declaration in the "
        "agent file neither replaced the project server nor started a server without the project declaration.",
    ),
    "codex-agent-mcp-startup-failure-isolation": (
        "mcp", "The generated subagent received the healthy read-only stdio fixture's result when a second "
        "server in the generated project config exited before completing its MCP handshake.",
    ),
    "codex-agent-mcp-required-startup-policy": (
        "mcp", "An unavailable optional stdio server let the generated subagent run. Requiring that "
        "enabled server blocked startup before any model request. Disabling it let the subagent run without "
        "starting the fixture.",
    ),
    "codex-agent-mcp-project-tool-filters": (
        "mcp", "The generated subagent discovered and called the read-only stdio tools allowed by native "
        "filters added to the staged project config. The deny list took precedence over the allow list, "
        "and calls by name to filtered tools did not reach the fixture.",
    ),
    "codex-project-subagent-selection": (
        "agents", "Two generated project definitions loaded their distinct developer instructions in one session. "
        "Withholding either definition left only the other generated agent available.",
    ),
    "codex-project-subagent-mcp-results": (
        "mcp", "Each generated agent called the same read-only stdio fixture through the generated project config "
        "and received only its own labeled result. Withholding the config left the fixture idle.",
    ),
    "codex-project-subagent-model-requests": (
        "models", "The scripted loopback endpoint received gpt-6-luna from the first generated agent and "
        "gpt-6.1-sol from the second in the same session.",
    ),
    "codex-project-subagent-effort-requests": (
        "reasoning", "The scripted loopback endpoint received high effort from the first generated agent and "
        "medium from the second in the same session.",
    ),
    "codex-generated-stdio-argument-roundtrip": (
        "mcp", "Codex started the read-only fixture from the unmodified CLI-generated config. The fixture "
        "received Windows paths with spaces and Unicode and a separate argument containing literal quotes.",
    ),
    "codex-mcp-list-project-trust": (
        "mcp", "The CLI listed the configured project server only with project trust and the generated file "
        "present. Inventory did not start the server.",
    ),
    "codex-agent-summary-inheritance": (
        "reasoning", "Leaving the child summary unset inherited the parent's detailed setting. Explicit none "
        "omitted the summary from the scripted local Responses request.",
    ),
    "codex-picker-summary-verbosity-matrix": (
        "reasoning", "For all nine visible signed-out models at high effort, the generated agent passed the "
        "selected summary and verbosity values to the scripted local Responses endpoint. Explicit none omitted summary.",
    ),
    "agent-model-effort-config-load": (
        "reasoning", "The direct runtime loader read the scalar model and effort into the selected agent definition.",
    ),
    "agent-model-array-rejected": (
        "models", "The direct runtime loader rejected the generated fallback array because it expected a scalar model.",
    ),
    "named-profile-model-request": (
        "models", "The CLI sent the generated named profile's model to a scripted loopback Responses endpoint.",
    ),
    "named-profile-effort-precedence": (
        "reasoning", "The scripted endpoint observed the profile effort and the higher-priority command-line override.",
    ),
    "codex-lmstudio-local-generation": (
        "models", "The signed-out CLI used the generated named profile and completed a Responses turn with "
        "a locally loaded LM Studio model. The temporary endpoint only forwarded requests to the local server.",
    ),
    "codex-lmstudio-reasoning-off": (
        "reasoning", "The generated profile sent effort none and completed a locally generated Responses turn "
        "without a reasoning item.",
    ),
    "claude-lmstudio-local-generation": (
        "models", "The CLI used a temporary copy of the generated settings with a loopback port override and "
        "completed a Messages turn with a locally loaded LM Studio model.",
    ),
    "agent-metadata-discovery": (
        "agents", "The bundled executable listed the generated project agent with its authored model selector.",
    ),
    "project-http-pending-approval": (
        "mcp", "The tested runtime read the generated project HTTP entry and reported it pending approval.",
    ),
    "project-public-docs-tool-discovery": (
        "mcp", "After local project approval, the bundled executable connected to OpenAI Docs and listed its tools.",
    ),
    "claude-child-model-request": (
        "models", "The generated subagent selected its own model in a request to a scripted loopback Messages endpoint.",
    ),
    "claude-child-effort-request": (
        "reasoning", "The generated subagent selected its own output_config.effort in the loopback request.",
    ),
    "claude-child-mcp-tool-filter": (
        "mcp", "The generated subagent tool list excluded the local fixture visible in the parent session.",
    ),
    "claude-cli-skill-use": (
        "skills", "The standalone CLI invoked the generated Skill and returned its instructions to the loopback endpoint.",
    ),
    "claude-cli-public-docs-tool-call": (
        "mcp", "The standalone CLI called a bounded read-only public OpenAI Docs tool after individual local approval.",
    ),
    "claude-cli-catalog-model-requests": (
        "models", "Every signed-out catalog selector sent its resolved model ID to a scripted Messages endpoint.",
    ),
    "claude-cli-catalog-effort-requests": (
        "reasoning", "Every model and effort pair advertised by the signed-out catalog sent its selected effort.",
    ),
    "claude-cli-default-child-model": (
        "models", "The catalog's resolved default model was pinned in the generated file and used by the subagent.",
    ),
    "claude-desktop-project-instructions": (
        "instructions", "The Desktop-pinned engine included the generated CLAUDE.md guidance in its first "
        "loopback request. Withholding the file removed the guidance.",
    ),
    "claude-desktop-skill-use": (
        "skills", "The Desktop-pinned engine invoked the generated Skill and included its instructions in "
        "a subsequent loopback request. Withholding the Skill removed those instructions.",
    ),
    "claude-desktop-catalog-model-requests": (
        "models", "The Desktop-pinned engine sent the resolved model IDs for all five signed-out selectors "
        "to a scripted Messages endpoint.",
    ),
    "claude-desktop-catalog-effort-requests": (
        "reasoning", "The Desktop-pinned engine sent all 25 signed-out selector and effort pairs to a "
        "scripted endpoint. Twenty used generated project settings and five max cases used explicit "
        "launch flags matching the packaged SDK's argument mapping.",
    ),
    "claude-ultracode-project-setting": (
        "reasoning", "The tested engine applied the generated Ultracode setting while preserving the "
        "selected effort. Project off overrode user on, and disabling workflows suppressed Ultracode. "
        "The scripted endpoint observed the resulting effort and workflow tool availability.",
    ),
    "qwen-daemon-agent-discovery": ("agents", "The daemon listed the generated project agent."),
    "qwen-daemon-mcp-pending-approval": (
        "mcp", "The daemon read the generated project server and kept it disconnected pending approval.",
    ),
    "qwen-daemon-approved-stdio-discovery": (
        "mcp", "The daemon connected and requested the fixture tool list after approval of the exact configuration.",
    ),
    "qwen-desktop-public-https-server-approval": (
        "mcp", "The bundled daemon connected and exposed five public OpenAI Docs tools after approval "
        "of the exact generated setting. Withholding approval, changing the setting after approval, "
        "or omitting it left the endpoint idle.",
    ),
    "qwen-desktop-public-https-tool-approval": (
        "mcp", "The bundled daemon requested approval for each attempt in the same session. Allow once "
        "returned public documentation URLs to the scripted local model endpoint. Rejecting the call "
        "before and after that turn returned no new documentation result.",
    ),
    "qwen-cli-public-https-server-approval": (
        "mcp", "The CLI discovered five public OpenAI Docs tools after approval of the generated setting. "
        "Withholding approval or the setting kept the endpoint idle. A change to the approved config "
        "required fresh approval.",
    ),
    "qwen-cli-public-https-tool-approval": (
        "mcp", "A headless run reported that the requested documentation tool required approval. "
        "Allowing that exact tool let it return public documentation URLs over the configured HTTPS connection.",
    ),
    "qwen-daemon-startup-model": ("models", "The daemon confirmed the generated startup request's exact model route."),
    "qwen-daemon-session-effort": ("reasoning", "Live session context reflected the generated model's effort setting."),
    "qwen-companion-skill-use": (
        "skills", "The Companion listed the generated Skill in the sidebar and its ACP session returned the Skill instructions.",
    ),
    "qwen-companion-model-selection": (
        "models", "The sidebar received both configured models and the bundled ACP process sent the generated parent and "
        "child model IDs to a scripted server.",
    ),
    "qwen-companion-reasoning-request": (
        "reasoning", "The bundled ACP process sent the configured low and high efforts for the parent and child models.",
    ),
    "qwen-companion-mcp-approval": (
        "mcp", "The Companion called the generated stdio tool after scoped approval. The unapproved and changed entries "
        "stayed disconnected.",
    ),
    "qwen-daemon-skill-use": (
        "skills", "The bundled daemon invoked the generated Skill and included its instructions in a loopback request.",
    ),
    "qwen-daemon-child-model-request": (
        "models", "The generated subagent used its own configured model in a scripted loopback Responses request.",
    ),
    "qwen-daemon-child-effort-request": (
        "reasoning", "The delegated subagent sent its selected model's high effort while the parent sent low.",
    ),
    "opencode-v2-disabled-mcp-load": ("mcp", "The V2 server read the generated MCP entry with discovery disabled."),
    "opencode-v2-model-variant-catalog": ("models", "The V2 server listed built-in model and variant IDs."),
    "opencode-v2-staged-run-alias": (
        "models", "Generated OpenCode run arguments selected two configured catalog aliases. "
        "The loopback endpoint received their distinct upstream model IDs.",
    ),
    "opencode-v2-staged-run-variant": (
        "reasoning", "The generated argument selector sent low, high, or xhigh and the matching named variant's "
        "request body setting. Both runs without a variant omitted those fields.",
    ),
    "opencode-v2-run-instructions": (
        "instructions", "A model request in every generated run included the root instruction bundle. "
        "The control with the instruction file removed omitted it and kept the selected variant.",
    ),
    "opencode-v2-agent-variant-load": (
        "reasoning", "The V2 server reported the generated subagent's exact provider, model, and named variant.",
    ),
    "opencode-v2-stdio-discovery": (
        "mcp", "The V2 server initialized the local fixture and requested its tool list after explicit enablement.",
    ),
    "opencode-v2-variant-effort-request": (
        "reasoning", "The CLI applied a named variant's reasoning effort in a request to a scripted loopback endpoint.",
    ),
    "opencode-v2-child-skill-use": (
        "skills", "The delegated subagent loaded its allowed Skill and received the Skill instructions.",
    ),
    "opencode-v2-child-mcp-filter": (
        "mcp", "The generated subagent's tool list excluded the stdio fixture visible to the parent session.",
    ),
    "local-chat-cli-model-selection": (
        "models", "Generated run arguments selected the manually configured model alias. LM Studio ran "
        "Qwen3 0.6B Q8_0 locally and the CLI returned the expected answer in both runs.",
    ),
    "local-chat-cli-reasoning-switch": (
        "reasoning", "The staged variants sent none and medium to LM Studio Chat Completions on loopback. "
        "The real model returned reasoning content for medium and no reasoning content for none. "
        "This tests the two selections on one local model.",
    ),
    "kilo-stdio-discovery": (
        "mcp", "Kilo initialized the selected project stdio fixture and requested its tools.",
    ),
    "kilo-model-variant-catalog": (
        "models", "Kilo listed built-in model IDs and a local fixture model and variant. Weaver imported their selectors.",
    ),
    "kilo-agent-variant-load": (
        "reasoning", "Kilo reported the generated subagent's configured model and named variant from a synthetic catalog.",
    ),
    "kilo-cli-variant-request": (
        "reasoning", "A named variant sent high reasoning effort to the local endpoint. Omitting the variant omitted the field.",
    ),
    "kilo-staged-run-model": (
        "models", "Weaver staged all 426 enabled built-in text chat selections offline. Two synthetic local "
        "model selections reached the loopback endpoint through generated run arguments.",
    ),
    "kilo-staged-run-variant": (
        "reasoning", "Three advertised named variants sent low, high and xhigh through the generated run arguments. "
        "The two runs without a variant omitted the effort field.",
    ),
    "kilo-staged-run-instructions": (
        "instructions", "The generated project instructions reached the loopback endpoint. Withholding the file "
        "removed the instructions and preserved the selected model variant.",
    ),
    "kilo-child-ambient-mcp": (
        "mcp", "The generated subagent could see the fixture tool explicitly allowed by the parent project.",
    ),
    "kilo-extension-subagent-discovery": (
        "agents", "The extension sent the generated subagent and its model to the sidebar's complete agent list.",
    ),
    "kilo-extension-model-selection": (
        "models", "The extension's sidebar model picker selected the synthetic local provider model.",
    ),
    "kilo-extension-variant-selection": (
        "reasoning", "The sidebar exposed the local model's named variant and displayed it after selection.",
    ),
    "kilo-cli-generated-mcp-default-off": (
        "mcp", "The generated disabled entries kept the local stdio fixture idle and made no connection to "
        "the public HTTPS MCP server.",
    ),
    "kilo-cli-public-https-mcp-call": (
        "mcp", "Enabling only the public HTTPS MCP entry exposed the explicitly allowed list_openai_docs tool "
        "and returned a documentation URL. Withholding the entry kept the server idle.",
    ),
    "kilo-extension-public-https-mcp-call": (
        "mcp", "The generated disabled HTTPS entry made no connection. An explicit sidebar connection exposed "
        "only the allowed list_openai_docs tool and returned a documentation URL.",
    ),
    "kilo-extension-session-model-request": (
        "models", "A sidebar session sent the selected synthetic model ID to the scripted loopback provider.",
    ),
    "kilo-extension-session-variant-request": (
        "reasoning", "The sidebar session's review-high variant sent high effort to the scripted loopback provider.",
    ),
    "kilo-desktop-generated-mcp-discovery": (
        "mcp", "The Desktop-bundled engine kept the generated disabled stdio server idle. Enabling it in a "
        "disposable project produced initialize and tools/list. Withholding the artifacts removed the server.",
    ),
    "kilo-desktop-project-instructions": (
        "instructions", "A Desktop-bundled engine session sent the generated project instructions to the local "
        "endpoint. Withholding the file removed its instructions from the request.",
    ),
    "kilo-desktop-subagent-stdio-call": (
        "mcp", "The generated subagent called the explicitly allowed read-only stdio tool after the server "
        "was enabled in a disposable project. The generated disabled entry left the fixture idle.",
    ),
    "kilo-desktop-subagent-model-request": (
        "models", "The Desktop-bundled engine sent the generated subagent's configured synthetic model ID to the "
        "local endpoint. Withholding the agent file removed the child request.",
    ),
    "kilo-gateway-public-model-catalog": (
        "models", "The unauthenticated v1 catalog returned 470 unique model IDs. Offline validation preserved "
        "all exact IDs, including 19 aliases.",
    ),
    "kilo-gateway-catalog-variant-validation": (
        "reasoning", "Offline validation preserved all 1,109 named variants across 332 models in the captured "
        "Gateway catalog and rejected an unadvertised variant for each model.",
    ),
    "kilo-gateway-client-model-route": (
        "models", "The exact selected Gateway model IDs reached a loopback endpoint. The captured model metadata "
        "was pinned in the disposable project for Chat Completions, Responses and Messages.",
    ),
    "kilo-gateway-client-variant-wire": (
        "reasoning", "With captured model metadata pinned locally, the CLI and session API sent matching "
        "DeepSeek instant and thinking settings, OpenAI xhigh effort, and Anthropic adaptive thinking with max effort.",
    ),
    "cline-agent-file-inventory": (
        "agents", "Cline listed the generated project agent file in its configuration inventory.",
    ),
    "cline-disabled-mcp-registration": (
        "mcp", "Cline listed the manually staged stdio entry as disabled and made no fixture requests.",
    ),
    "cline-session-model-request": (
        "models", "The generated CLI arguments selected the OpenRouter model ID sent to a scripted loopback endpoint.",
    ),
    "cline-session-effort-request": (
        "reasoning", "The generated CLI arguments sent the selected thinking level to the scripted endpoint.",
    ),
    "cline-child-skill-use": (
        "skills", "The configured subagent called its allowed Skill and received the generated Skill instructions.",
    ),
    "cline-child-mcp-filter": (
        "mcp", "The configured subagent received the selected built-in tools. The parent alone could see its MCP fixture.",
    ),
    "cline-cli-generated-mcp-approval": (
        "mcp", "Weaver's generated --auto-approve false kept the enabled stdio tool discoverable. "
        "The headless call was withheld with an empty, sibling-only, or exact per-server autoApprove entry.",
    ),
    "cline-cli-global-mcp-auto-approval": (
        "mcp", "The disabled generated server stayed idle with global auto-approval enabled. "
        "After enabling the server, global auto-approval called the read-only fixture regardless of its "
        "per-server autoApprove entries. Omitting the global flag also allowed the call in the tested Act session.",
    ),
    "cline-cli-public-https-mcp-tool-call": (
        "mcp", "The generated disabled and omitted entries left the public endpoint idle. After enabling the server, "
        "both approval settings exposed five tools. Explicit global auto-approval returned a public documentation URL "
        "from list_openai_docs to the scripted loopback model.",
    ),
    "cline-cli-lmstudio-staged-model": (
        "models", "Cline selected the exact loaded LM Studio alias from a verified catalog using generated "
        "session arguments. Both provider paths completed an actual local Qwen3 0.6B Q8_0 turn.",
    ),
    "cline-cli-lmstudio-staged-reasoning-off": (
        "reasoning", "The generated explicit-off arguments selected Cline's OpenAI-compatible provider and sent "
        "none to LM Studio. The local model returned the answer without reasoning content. "
        "The unmodified default selection streamed reasoning content for this model.",
    ),
    "cline-cli-native-lmstudio-thinking-omission": (
        "reasoning", "Cline's lmstudio provider omitted the effort field for --thinking none on the tested model. "
        "The server returned reasoning content under its default setting. The OpenAI-compatible provider sent none "
        "and suppressed reasoning content in the separate control.",
    ),
    "cline-desktop-public-https-mcp-tool-call": (
        "mcp", "The packaged Desktop backend listed the generated disabled server without connecting. "
        "After explicit enablement and local session auto-approval, it discovered five tools and returned public "
        "documentation from list_openai_docs. The omitted control had no entry or HTTPS connection.",
    ),
    "cline-desktop-model-catalog": (
        "models", "The Desktop backend listed its bundled OpenRouter model IDs and reasoning capability flags.",
    ),
    "cline-desktop-session-model-request": (
        "models", "The Desktop session sent the selected OpenRouter model ID to a scripted loopback endpoint.",
    ),
    "cline-desktop-session-effort-request": (
        "reasoning", "The Desktop session applied the selected thinking effort in its loopback model request.",
    ),
    "cline-desktop-max-effort-fallback": (
        "reasoning", "Desktop 0.0.49 sent medium effort when its chat backend received max for the tested model.",
    ),
    "cline-desktop-lmstudio-local-generation": (
        "models", "The Desktop sidecar listed and selected the loaded LM Studio alias for Qwen3 0.6B Q8_0. "
        "The answer returned by the local server matched the final answer in the session history.",
    ),
    "cline-desktop-lmstudio-session-reasoning": (
        "reasoning", "The Desktop sidecar sent medium effort to the loaded local model. "
        "The response streamed reasoning content and the session history recorded a separate reasoning field.",
    ),
    "cline-desktop-lmstudio-reasoning-off": (
        "reasoning", "Turning thinking off sent none through the Desktop sidecar's native LM Studio provider. "
        "The local model completed its answer without reasoning content, and the session history had no reasoning field.",
    ),
    "cline-ide-stdio-activation": (
        "mcp", "The IDE loaded the generated disabled entry and connected and discovered its tool only after "
        "enabling the server in the isolated extension profile.",
    ),
    "cline-ide-mcp-category-auto-approval": (
        "mcp", "After enabling the generated stdio server, the sidebar's Use MCP servers category "
        "allowed the fixture tool to run while the tool's own auto-approval setting was off.",
    ),
    "cline-ide-stdio-manual-approval": (
        "mcp", "With MCP category auto-approval off, Reject withheld the fixture call and returned a denial. "
        "Approve in a separate task sent one call and returned the fixture result. Disabling the server "
        "removed the tool from the next model request.",
    ),
    "cline-ide-public-https-tool-inventory": (
        "mcp", "The generated disabled OpenAI Docs entry made no connection and exposed no tools. "
        "Enabling it in the isolated extension host discovered five tools over Streamable HTTPS. "
        "Disabling it again removed the tools from the next request.",
    ),
    "cline-ide-public-https-tool-approval": (
        "mcp", "Category auto-approval let list_openai_docs return a public documentation URL with "
        "per-tool auto-approval off. With the category off, Reject returned a denial and Approve returned "
        "a documentation URL in a separate task.",
    ),
    "cline-ide-delegation-unavailable": (
        "agents", "The isolated IDE exposed subagents as disabled and offered no delegation tool in the test request.",
    ),
    "cline-ide-local-model-selection": (
        "models", "The extension listed two loopback provider fixture models and sent the selected ID in a chat request.",
    ),
    "cline-ide-reasoning-effort-request": (
        "reasoning", "The extension applied high effort in a scripted LM Studio Chat Completions request.",
    ),
    "factory-droid-discovery": (
        "agents", "Droid advertised the generated custom droid. The unsigned session stopped at daemon authentication.",
    ),
    "factory-cli-skill-use": (
        "skills", "Droid called the project Skill and received the generated Skill instructions.",
    ),
    "factory-cli-context7-https-tool-inventory": (
        "mcp", "Droid connected to the exact generated project Context7 entry over Streamable HTTPS "
        "and discovered both tools. The read-only exec inventory applied exact inclusion and exclusion "
        "filters for the resolver. Withholding the config removed the server and both tools.",
    ),
    "factory-cli-context7-https-tool-call": (
        "mcp", "Droid used the generated project Context7 entry to resolve a public library over Streamable HTTPS. "
        "The scripted loopback endpoint received the result. Removing the config or excluding the resolver "
        "from the exact tool selection prevented the call.",
    ),
    "factory-cli-spec-model-request": (
        "models", "With generated arguments, Droid sent the selected specification model to a scripted local endpoint. "
        "The execution-only control sent the independently selected execution model.",
    ),
    "factory-cli-spec-effort-request": (
        "reasoning", "Droid sent the explicitly selected specification effort and resolved its own effort when omitted. "
        "Weaver rejected an effort absent from the saved catalog before launch.",
    ),
    "factory-cli-spec-approval-model-transition": (
        "models", "Droid used the generated JSON-RPC settings to select the planning model. A one-time plan approval "
        "switched the same session to the generated execution model. Cancelling sent no execution request.",
    ),
    "factory-cli-spec-approval-effort-transition": (
        "reasoning", "The scripted endpoint observed the selected planning effort before approval and the independently "
        "selected execution effort after approval.",
    ),
    "factory-cli-model-catalog": (
        "models", "The signed-out JSON-RPC catalog listed model IDs and their advertised reasoning efforts.",
    ),
    "factory-cli-byok-base-model-catalog": (
        "reasoning", "Nine bracketed BYOK selectors reported the same reasoning options and default as their "
        "configured base models. Weaver preserved their exact IDs when staging arguments and supported agent pins.",
    ),
    "factory-cli-byok-alias-model-requests": (
        "models", "Droid resolved nine generated bracketed BYOK selectors to their configured model IDs "
        "in 48 signed-out Chat Completions requests to a scripted loopback endpoint.",
    ),
    "factory-cli-byok-alias-effort-requests": (
        "reasoning", "Explicit efforts reached the scripted endpoint, with no effort value for off or none. "
        "Omitting the argument sent high for all nine aliases, including five with different catalog defaults.",
    ),
    "factory-cli-project-instructions-in-request": (
        "instructions", "The scripted endpoint received a line from the generated AGENTS.md in all 48 "
        "BYOK requests. Withholding the file removed the line in the control.",
    ),
    "factory-cli-custom-model-request": (
        "models", "The CLI accepted the catalog's custom selector and sent its model ID to a scripted local endpoint.",
    ),
    "factory-cli-custom-effort-request": (
        "reasoning", "Custom-model exec arguments sent low, medium, and high, and off omitted the effort field.",
    ),
    "kimi-web-stdio-tool-discovery": (
        "mcp", "Kimi's workspace registry loaded the project MCP entry after trust and initiated stdio tool discovery.",
    ),
    "kimi-web-skill-activation": (
        "skills", "The native session API activated the generated workspace Skill. The scripted loopback endpoint "
        "received its full instructions. Withholding the Skill file made activation fail before a provider request.",
    ),
    "kimi-project-stdio-call": (
        "mcp", "The trusted session used the byte-for-byte generated project MCP file and returned a stdio "
        "fixture result under an exact tool allow rule. An exact deny rule blocked the call. Untrusted and "
        "missing-config runs left the fixture idle.",
    ),
    "kimi-project-context7-https-call": (
        "mcp", "A trusted session used the exact generated project config and returned the public React library "
        "ID through Context7 over Streamable HTTPS. An exact tool deny rule rejected the same call. The tool "
        "was unavailable without workspace trust or without the project config.",
    ),
    "kimi-web-model-catalog": (
        "models", "The local Web API enumerated three configured fixture aliases with their advertised effort lists.",
    ),
    "kimi-web-session-model-effort": (
        "reasoning", "The staged session request bound the named model and high effort in a loopback request.",
    ),
    "kimi-web-subagent-pool-effort": (
        "reasoning", "A spawned custom subagent used an alternate pool model and the shared high effort.",
    ),
    "qoder-cli-stdio-healthcheck": (
        "mcp", "The signed-out CLI connected, initialized, and pinged the generated project stdio fixture.",
    ),
    "qoder-cli-context7-https-healthcheck": (
        "mcp", "The signed-out CLI connected to the generated Context7 entry over HTTPS using the host TLS root.",
    ),
    "qoder-cli-acp-authentication": (
        "models", "The signed-out CLI completed the ACP handshake and required authentication before creating "
        "a session. The probe sent no model prompt.",
    ),
    "qoder-cli-thinking-flag-discovery": (
        "reasoning", "CLI help listed thinking mode and fixed-budget flags. Generated session arguments reached "
        "the sign-in requirement and an unknown-flag control failed during argument parsing. Effective thinking is unverified.",
    ),
    "qoder-ide-agent-inventory": (
        "agents", "The bundled IDE backend listed the generated project agent with its exact instruction body. "
        "The empty workspace lacked the entry.",
    ),
    "qoder-ide-stdio-settings-import": (
        "mcp", "The bundled IDE backend imported the generated settings fragment and discovered the stdio fixture's tool. "
        "The entry survived a restart, disabling stopped discovery, and deletion removed it.",
    ),
    "qoder-ide-public-https-settings-import": (
        "mcp", "The bundled IDE backend imported the generated OpenAI Docs entry and discovered five tools over HTTPS. "
        "A fresh backend rediscovered the tools. Disabled and removed entries opened no connection to the server.",
    ),
    "qoder-ide-signed-out-model-inventory": (
        "models", "The isolated backend opened an empty session and returned no model metadata while signed out.",
    ),
    "air-ide-acp-registration-load": (
        "agents", "The bundled configuration loader preserved the generated executable, arguments, and separate IDE "
        "and custom MCP settings. It dropped the documented WSL execution field in the control.",
    ),
    "air-desktop-acp-registration-load": (
        "agents", "The standalone build's ACP parser loaded the staged display name, executable, and "
        "literal arguments. The empty configuration had no agents, and the IDE MCP switch control "
        "produced the same standalone entry.",
    ),
    "air-desktop-mcp-config-parse": (
        "mcp", "The standalone project parser recognized the generated HTTPS and disabled stdio servers, "
        "preserved argument boundaries and the empty argument, and reported no auto-approved tools.",
    ),
    "air-desktop-skill-file-parse": (
        "skills", "The standalone build's bundled Skill parser read the full staged files and their paths "
        "and recovered all six names and descriptions.",
    ),
    "air-ide-mcp-config-parse": (
        "mcp", "The bundled MCP parser resolved the generated HTTPS and stdio entries. The empty file yielded no servers.",
    ),
    "project-stdio-healthcheck": (
        "mcp", "The bundled executable connected to the generated project stdio fixture after isolated user approval.",
    ),
    "signed-out-model-list": (
        "models", "The bundled executable listed signed-out model aliases and their resolved IDs.",
    ),
    "signed-out-effort-options": (
        "reasoning", "The bundled executable advertised effort levels for the signed-out model aliases.",
    ),
    "lmstudio-local-model-inventory": (
        "models", "Weaver imported the local LLM and embedding model from the native inventory and retained the "
        "loaded instance and its context length.",
    ),
    "lmstudio-local-reasoning-switch": (
        "reasoning", "The generated on and off selectors changed the reasoning content returned by native chat. "
        "Both Weaver and the server rejected high for this model.",
    ),
    "lmstudio-compatible-reasoning-switch": (
        "reasoning", "For Qwen3 0.6B, the compatible Responses and Chat endpoints disabled thinking at effort none. "
        "The server logged fallback to on for unsupported low and high efforts; native chat rejected high.",
    ),
    "lmstudio-messages-thinking-switch": (
        "reasoning", "The Messages endpoint produced a thinking block with adaptive and a plain text response with disabled "
        "for the locally loaded Qwen3 0.6B model.",
    ),
    "lmstudio-loaded-chat-alias": (
        "models", "The generated selector used the exact custom loaded instance ID from the native list. "
        "A fresh catalog after unload no longer supplied the alias's capabilities.",
    ),
    "lmstudio-loaded-chat-alias-reasoning": (
        "reasoning", "For a Qwen3 loaded alias, generated native chat requests returned a message with off "
        "and a reasoning block with on. The generated Responses request disabled reasoning at effort none.",
    ),
    "lmstudio-native-mcp-tool-scope": (
        "mcp", "The generated native chat request called the selected Context7 resolver. Selecting only query-docs "
        "prevented the resolver call in the control.",
    ),
    "lmstudio-responses-mcp-tool-scope": (
        "mcp", "The generated Responses request listed and called only the selected Context7 tool. "
        "The resolver returned a public React library ID when selected.",
    ),
    "lmstudio-native-preconfigured-stdio": (
        "mcp", "The authenticated native chat request used the generated plugin reference to call a preconfigured "
        "stdio server. The fixture received only the exact tool selected for each request.",
    ),
    "lmstudio-preconfigured-plugin-authentication": (
        "mcp", "The loopback server rejected a missing token and a valid token without plugin access. "
        "Neither rejected request reached the stdio fixture.",
    ),
    "lmstudio-desktop-preset-prompt-parse": (
        "instructions", "The parser extracted from LM Studio Desktop 0.4.28 build 2 accepted the generated "
        "system prompt in four Presets. It rejected the missing-name control and removed a privileged load field. "
        "The probe exercised the parser in isolation.",
    ),
    "lmstudio-desktop-preset-reasoning-parse": (
        "reasoning", "The bundled Desktop parser retained the generated thinking toggle and nonnegative "
        "reasoning budget, both alone and together. Model application and inference remain unchecked.",
    ),
    "lmstudio-desktop-mcp-config-parse": (
        "mcp", "The bundled Desktop MCP parser accepted the empty default, retained the selected HTTPS "
        "URL and stdio command and arguments, and rejected a string in place of the argument array.",
    ),
    "lmstudio-desktop-bundled-stdio-tool-call": (
        "mcp", "The isolated Desktop session module started the configured stdio fixture, listed both tools, "
        "and returned the result of the single allowlisted tool. The fixture logged exactly one call.",
    ),
    "lmstudio-desktop-bundled-http-wire": (
        "mcp", "The bundled Streamable HTTP client initialized, discovered tools and completed one allowlisted "
        "call over live loopback HTTP after a test-only scheme override. The exact staged HTTPS URL also "
        "completed the scripted fetch exchange. Live TLS and the Desktop UI remain unchecked.",
    ),
    "lmstudio-desktop-bundled-skill-tool": (
        "skills", "The isolated Desktop MCP client parsed the generated fragment, launched the staged stdio "
        "Skill server and retrieved the exact selected instructions through load_skill. The probe ran without "
        "a model turn. Desktop UI activation and model use remain unverified.",
    ),
    "lmstudio-bionic-skill-import": (
        "skills", "Bionic's bundled local importer inspected, saved and reopened all six staged Skills in "
        "isolation. The names, descriptions and instruction bodies survived the import.",
    ),
    "lmstudio-bionic-settings-skill-import": (
        "skills", "Bionic's Skills settings imported all six staged SKILL.md files in a disposable profile. "
        "The test supplied file selections at the Electron dialog boundary. A canceled selection installed "
        "nothing, and all completed imports preserved their metadata and normalized instructions.",
    ),
    "lmstudio-bionic-composer-skill-reference": (
        "skills", "All six imported Skills appeared when searched by name in Bionic's composer reference "
        "picker. Selecting one added its reference to the unsent draft without a model request.",
    ),
    "lmstudio-bionic-mcp-form-parse": (
        "mcp", "The bundled form converter and schema retained the staged HTTPS URL and the stdio command and "
        "arguments. Bionic filtered an empty argument in the control.",
    ),
    "lmstudio-bionic-mcp-stdio-discovery": (
        "mcp", "After a native process started with redirected Electron paths, the isolated disabled fixture "
        "stayed idle. The enabled entry preserved its arguments, initialized and listed its tool.",
    ),
    "lmstudio-bionic-local-model-picker": (
        "models", "The signed-out Windows Bionic app indexed and selected the local Qwen3 0.6B Q8_0 "
        "model in its session picker. The model was absent in a separate empty profile.",
    ),
    "lmstudio-bionic-local-reasoning-picker": (
        "reasoning", "The Bionic session picker switched the local Qwen3 0.6B model between Off and On. "
        "Weaver staged both labels from the observed model mapping and refused high as an unavailable menu choice.",
    ),
    "lmstudio-bionic-local-model-inference": (
        "models", "The signed-out Bionic session dispatched the indexed Qwen3 0.6B Q8_0 GGUF to the local "
        "llama.cpp CPU runtime and saved the expected arithmetic result in two disposable sessions.",
    ),
    "lmstudio-bionic-local-reasoning-output": (
        "reasoning", "Bionic saved text output with reasoning Off. With reasoning On, the saved assistant "
        "message contained a separate reasoning part alongside its text answer.",
    ),
    "lmstudio-bionic-session-stdio-tool-call": (
        "mcp", "A local Qwen3 session used Bionic's on-demand MCP discovery and called the read-only stdio fixture "
        "once. The saved tool result matched the wire payload. The model's final text did not repeat that result.",
    ),
    "lmstudio-bionic-session-mcp-disable": (
        "mcp", "The disabled fixture stayed idle at startup. Enabling it exposed the integration mention in "
        "the composer. Disabling it after the tool call removed the mention and produced no further calls.",
    ),
    "lmstudio-bionic-settings-https-tool-discovery": (
        "mcp", "Bionic saved Weaver's public documentation endpoint through Settings > MCP and completed "
        "Streamable HTTPS initialization and discovery of five tools without an Authorization header.",
    ),
    "lmstudio-bionic-settings-https-pause": (
        "mcp", "Saving the public server paused kept it idle. Two enable cycles initialized and listed tools. "
        "The UI returned to Paused when the server was switched off.",
    ),
    "lmstudio-bionic-settings-stdio-tool-discovery": (
        "mcp", "The Bionic settings form saved the generated command and all five separate arguments. "
        "Four enable cycles each initialized the stdio fixture and listed its read-only tool. "
        "The fixture received a value with a space as one argument.",
    ),
    "lmstudio-bionic-settings-stdio-pause": (
        "mcp", "The generated stdio fixture stayed idle when saved paused. Process checks observed the fixture "
        "exit on pause in two cycles. Reopening the profile kept the server paused and produced no fixture traffic.",
    ),
    "lmstudio-native-chat-context-length": (
        "models", "The generated native chat request loaded a downloaded model key at a new context length. "
        "A loaded alias accepted its existing length, and Weaver rejected a resize against the captured alias.",
    ),
    "lmstudio-local-embedding-request": (
        "models", "The generated embedding selector returned a vector from the local OpenAI-compatible endpoint.",
    ),
    "lmstudio-ephemeral-mcp-tool-call": (
        "mcp", "The local model called Context7 over HTTPS from the generated native chat integration. The server "
        "rejected a non-public address before connecting.",
    ),
    "lmstudio-compatible-chat-requests": (
        "models", "The generated model selector worked with the local Chat Completions, Responses and Messages endpoints.",
    ),
    "lmstudio-decision-instance-inventory": (
        "models", "Weaver retained the decision model type, vision-adapter flag, loaded alias and context length. "
        "Staging rejected the alias after the model was unloaded.",
    ),
    "lmstudio-local-decision-requests": (
        "models", "Both local decision endpoints accepted the generated instance selector and returned typed "
        "answers. Requests for a missing instance failed.",
    ),
    "kiro-v3-project-skill-discovery": (
        "skills", "ACP reported the generated project's Skill and steering paths. The empty workspace did "
        "not report either file.",
    ),
    "kiro-ide-steering-load": (
        "instructions", "The bundled extension read the exact generated project steering file. "
        "The empty and untrusted workspaces did not expose it.",
    ),
    "kiro-ide-skill-resource-load": (
        "skills", "The bundled extension resolved the generated project Skill and read its name and scope. "
        "The empty workspace did not resolve it.",
    ),
    "kiro-ide-agent-profile-load": (
        "agents", "The bundled extension loaded the generated reviewer and its prompt, read-only tool list, "
        "and MCP and Powers inheritance switches. The empty workspace exposed no reviewer.",
    ),
    "kiro-ide-agent-model-readback": (
        "models", "The bundled extension preserved the generated reviewer's claude-sonnet-4.6 model pin.",
    ),
    "kiro-ide-mcp-declarations": (
        "mcp", "The bundled extension parsed the generated HTTPS and stdio entries and their tool filters. "
        "The untrusted workspace and the empty config exposed no project servers.",
    ),
    "kiro-ide-https-discovery-and-call": (
        "mcp", "The extracted extension opened a native Streamable HTTPS connection to the generated "
        "Context7 endpoint, listed tools, and received a public React library ID from resolve-library-id.",
    ),
    "kiro-ide-https-disabled-tool-status": (
        "mcp", "The bundled session inventory marked query-docs disabled with the generated filter and "
        "enabled without it. The loader marked the disabled server and omitted untrusted or absent settings.",
    ),
    "kiro-ide-mcp-stdio-tool-filter": (
        "mcp", "The bundled MCP connection listed both read-only fixture tools. The session inventory marked "
        "only the named tool disabled, and a direct call of the enabled tool returned the fixture result.",
    ),
    "kiro-v3-workspace-agent-selection": (
        "agents", "ACP listed the generated agent as a workspace mode and switched to it. The empty "
        "workspace reported that the agent was missing.",
    ),
    "kiro-v3-stdio-activation": (
        "mcp", "The generated disabled fixture received no messages. After explicit enablement in the "
        "disposable project, the fixture received initialize and tools/list. Selecting the generated "
        "reviewer removed the fixture from the session's MCP status.",
    ),
    "kiro-v3-stdio-disabled-tool-status": (
        "mcp", "After explicit enablement of the staged stdio server, ACP marked only the selected tool "
        "disabled. The unfiltered control listed both tools as enabled and the disabled server received no messages.",
    ),
    "kiro-v3-https-disabled-tool-status": (
        "mcp", "The disabled Context7 entry stayed disabled. After explicit enablement, ACP connected over "
        "HTTPS and marked only query-docs disabled. Both tools were enabled in the unfiltered control.",
    ),
    "kiro-v3-model-login-required": (
        "models", "The signed-out CLI required login for model listing and for the generated session "
        "launch. The isolated ACP session did not advertise a model picker.",
    ),
    "kiro-v3-workspace-effort-readback": (
        "reasoning", "The CLI read back the exact staged Claude Sonnet 4.6 max effort default. The "
        "empty workspace had no default. The check covered settings readback.",
    ),
    "kiro-v3-workspace-output-limit-readback": (
        "models", "The signed-out V3 CLI read back the generated per-model output limit, including "
        "limit-only settings and settings combined with effort and thinking. The empty workspace had no defaults.",
    ),
    "kiro-v3-workspace-thinking-readback": (
        "reasoning", "The CLI read back disabled, between-tools, and adaptive thinking settings, including both "
        "adaptive visibility settings. An empty workspace had no model defaults. No model request was made.",
    ),
    "kiro-v3-documented-effort-matrix-readback": (
        "reasoning", "The signed-out CLI read back all 51 documented model and default-effort pairs across ten "
        "models. The 94 readbacks also covered thinking modes, display controls and output bounds. An empty "
        "workspace had no model defaults. No model request was made.",
    ),
    "kiro-v3-acp-initial-model-selection": (
        "models", "The signed-out ACP session returned gpt-5.6-terra as its selected model after receiving the "
        "staged session options. The session created without those options advertised no selected model.",
    ),
    "kiro-v3-acp-signed-out-effort-boundary": (
        "reasoning", "The staged initial ACP options carried xhigh. The signed-out runtime exposed no effective "
        "effort option or service model catalog. The probe sent no model prompts.",
    ),
    "hermes-profile-role-loading": (
        "agents", "The local distribution planner accepted the generated manifest and the prompt "
        "loader read the generated SOUL.md from a disposable profile.",
    ),
    "hermes-profile-skill-view": (
        "skills", "The CLI listed the generated profile Skill, and the native Skill tool read it. "
        "The empty profile did not expose that Skill.",
    ),
    "hermes-profile-stdio-tool-call": (
        "mcp", "The generated disabled entry received no messages. Explicitly enabling it in the "
        "disposable profile allowed discovery and a native tool dispatch that returned the fixture result.",
    ),
    "hermes-profile-model-read": (
        "models", "The native profile loader read the configured synthetic model and its OpenRouter provider.",
    ),
    "hermes-profile-effort-resolution": (
        "reasoning", "The native resolver applied the generated OpenRouter model-specific high effort override.",
    ),
    "hermes-lmstudio-local-generation": (
        "models", "The native engine loaded the generated profile and role, and completed a Chat Completions turn "
        "with an LM Studio model on loopback.",
    ),
    "hermes-lmstudio-gateway-generation": (
        "models", "The one-shot CLI used a disposable foreground host gateway to run the generated named profile "
        "and role with an LM Studio model. The usage receipt confirmed completion and the selected model.",
    ),
    "hermes-lmstudio-gateway-reasoning-off": (
        "reasoning", "The one-shot CLI retained the generated per-model override through the host gateway. "
        "LM Studio received reasoning_effort none and returned a response without reasoning content.",
    ),
    "hermes-lmstudio-reasoning-off": (
        "reasoning", "The generated per-model override made the Hermes engine send reasoning_effort none. "
        "The local response completed without reasoning content.",
    ),
    "zed-stdio-explicit-activation": (
        "mcp", "The exact generated disabled entry and the absent entry sent no stdio messages. "
        "Enabling the fixture in disposable Zed user settings produced initialize and tools/list.",
    ),
    "zed-lmstudio-v0-catalog-request": (
        "models", "Zed read the configured LM Studio provider base and requested /models from a loopback fixture. "
        "A separate base-path control received the changed route. Editor startup stopped at home directory resolution.",
    ),
    "zed-public-https-mcp-tool-inventory": (
        "mcp", "The signed-out Windows editor initialized the public OpenAI Docs MCP server over HTTPS and "
        "received its five-tool inventory after explicit enablement in disposable user settings. The generated "
        "disabled entry and the omitted control made no connection to the server.",
    ),
    "zed-settings-ui-disabled-mcp-inventory": (
        "mcp", "The signed-out Windows editor displayed the generated server with its toggle off. "
        "The control without the generated entry showed an empty inventory. Neither run started the fixture.",
    ),
    "openhands-headless-skill-invocation": (
        "skills", "The headless CLI included the generated project instructions and Skill in the "
        "model context. Its invoke_skill tool returned the generated Skill body.",
    ),
    "openhands-headless-agent-task": (
        "agents", "The headless task tool launched the generated file-based reviewer and returned its "
        "result. The reviewer exposed its base finish and think tools.",
    ),
    "openhands-headless-stdio-tool-call": (
        "mcp", "The generated disabled fixture and the absent control sent no messages. After explicit "
        "enablement in disposable settings, the fixture initialized, listed its tool, and returned its sentinel.",
    ),
    "openhands-named-profile-model-request": (
        "models", "The generated reviewer resolved its named local LLM profile and used its configured "
        "model in the scripted loopback Responses request.",
    ),
    "openhands-named-profile-effort-request": (
        "reasoning", "The scripted loopback Responses endpoint observed different configured reasoning "
        "efforts from the parent and the generated reviewer.",
    ),
    "canvas-disabled-mcp-settings": (
        "mcp", "The pinned local Agent Server persisted the generated stdio and HTTPS bodies disabled, "
        "rejected a duplicate ID, and excluded the disabled entries from enabled servers.",
    ),
    "canvas-stdio-test-tool-call": (
        "mcp", "The pinned local Agent Server explicitly enabled the disposable stdio entry, then "
        "its MCP test endpoint discovered and called the fixture before the entry was disabled again.",
    ),
    "canvas-backend-project-skill-load": (
        "skills", "The pinned local backend SDK loaded the staged repository instructions and Skill "
        "from a disposable Git project. The empty project exposed neither artifact.",
    ),
    "canvas-agent-profile-materialization": (
        "agents", "The local Agent Server stored the staged role through its manual profile path and "
        "resolved its named LLM profile and selected MCP scope in launch diagnostics.",
    ),
    "canvas-llm-profile-preflight-model": (
        "models", "A scripted loopback endpoint received the selected model ID during the local "
        "Agent Server LLM profile preflight.",
    ),
    "canvas-llm-profile-preflight-effort": (
        "reasoning", "The local Agent Server exposed reasoning settings in its schema and sent the "
        "configured low effort in the preflight Chat Completions request once chat mode was explicit.",
    ),
    "canvas-conversation-agent-profile": (
        "agents", "A local Agent Server conversation launched by saved profile ID received the staged "
        "role instructions and completed using scripted loopback responses.",
    ),
    "canvas-conversation-skill-invocation": (
        "skills", "The conversation advertised the generated project Skill and returned its body "
        "from invoke_skill to the loopback provider.",
    ),
    "canvas-conversation-stdio-tool-call": (
        "mcp", "The generated disabled stdio entry sent no fixture messages. The conversation launched "
        "after explicit enablement discovered and called the fixture and received its sentinel.",
    ),
    "canvas-conversation-context7-tool-call": (
        "mcp", "The saved profile sent no HTTPS requests with the generated Context7 entry disabled. "
        "After explicit enablement, the conversation called resolve-library-id over Streamable HTTPS "
        "and received the public React library ID.",
    ),
    "canvas-conversation-model-request": (
        "models", "The local conversation sent the named LLM profile's model in every scripted "
        "Chat Completions turn.",
    ),
    "canvas-conversation-effort-request": (
        "reasoning", "The local conversation sent the named profile's configured low reasoning effort "
        "in every scripted Chat Completions turn.",
    ),
}
_OBSERVATION_FIELDS = {
    "product_id", "surface_id", "product_version", "engine_version", "os_id", "os_variant",
    "architecture", "interface", "environment", "checked_on", "source_commit", "checks",
}
_OBSERVATION_INTERFACES = {
    ("openrouter", "gateway", "public-model-list"),
    ("openrouter", "gateway", "public-provider-endpoint-list"),
    ("antigravity", "cli", "headless-loopback"),
    ("antigravity", "cli", "acp-stdio"),
    ("antigravity", "desktop", "bundled-language-server"),
    ("antigravity", "cli", "headless-adc"),
    ("antigravity", "ide", "bundled-language-server"),
    ("antigravity", "sdk", "python-config-constructor"),
    ("codex", "cli", "app-server-stdio"),
    ("codex", "cli", "exec-loopback"),
    ("codex", "cli", "mcp-list"),
    ("codex", "desktop", "app-server-stdio"),
    ("chatgpt", "desktop", "desktop-bundled-plugin-reader"),
    ("chatgpt", "web", "public-web-ui"),
    ("chatgpt", "web", "hosted-chat-ui"),
    ("chatgpt", "work-local", "desktop-bundled-plugin-reader"),
    ("chatgpt", "work-cloud", "hosted-work-ui"),
    ("codex", "ide", "app-server-stdio"),
    ("copilot", "vscode", "bundled-runtime"),
    ("claude-code", "vscode", "bundled-runtime"),
    ("claude-code", "cli", "stream-json"),
    ("claude-code", "cli", "print-json"),
    ("claude-code", "desktop", "desktop-pinned-runtime"),
    ("devin", "desktop-local", "desktop-bundled-acp-stdio"),
    ("devin", "desktop-local", "desktop-bundled-config-inventory"),
    ("qwen-code", "cli", "daemon-http"),
    ("qwen-code", "cli", "exec-loopback"),
    ("qwen-code", "cli", "exec-public-https-mcp"),
    ("qwen-code", "desktop", "bundled-daemon-http"),
    ("qwen-code", "desktop", "bundled-daemon-public-https-mcp"),
    ("perplexity", "app", "public-web-ui"),
    ("perplexity", "agent-api", "unauthenticated-catalog-http"),
    ("perplexity", "orchestrator", "unauthenticated-catalog-http"),
    ("perplexity", "orchestrator", "openai-sdk"),
    ("perplexity", "orchestrator", "anthropic-sdk"),
    ("qwen-code", "vscode", "vscode-extension-host"),
    ("opencode", "cli", "serve-http"),
    ("opencode", "cli", "exec-loopback"),
    ("opencode", "cli", "exec-local-inference"),
    ("kilo", "cli", "serve-http"),
    ("kilo", "cli", "exec-loopback"),
    ("kilo", "cli", "exec-local-inference"),
    ("kilo", "ide", "vscode-extension-host"),
    ("kilo", "desktop", "serve-http"),
    ("kilo", "gateway", "public-model-list"),
    ("kiro", "cli", "acp-stdio"),
    ("kiro", "cli", "config-inventory"),
    ("kiro", "ide", "bundled-extension-loaders"),
    ("hermes", "cli", "config-inventory"),
    ("hermes", "cli", "direct-engine"),
    ("hermes", "cli", "oneshot-gateway"),
    ("zed", "ide", "editor-startup"),
    ("zed", "ide", "editor-settings-ui"),
    ("openhands", "cli", "headless-loopback"),
    ("openhands", "canvas", "local-agent-server-asgi"),
    ("cursor", "cli", "mcp-cli-inventory"),
    ("cursor", "cli", "bundled-subagent-loader"),
    ("cursor", "cli", "bundled-skill-loader"),
    ("cursor", "ide", "ide-bundled-config-loaders"),
    ("jetbrains-air", "ide", "ide-bundled-config-loaders"),
    ("jetbrains-air", "desktop", "desktop-bundled-config-loaders"),
    ("cline", "cli", "config-inventory"),
    ("cline", "cli", "exec-loopback"),
    ("cline", "cli", "exec-local-inference"),
    ("cline", "cli", "exec-mcp-approval"),
    ("cline", "desktop", "desktop-sidecar"),
    ("cline", "ide", "vscode-extension-host"),
    ("cline", "ide", "vscode-extension-mcp-approval"),
    ("factory", "cli", "exec-loopback"),
    ("factory", "cli", "exec-jsonrpc"),
    ("factory", "cli", "mcp-cli-inventory"),
    ("kimi-code", "cli", "local-web-api"),
    ("kimi-code", "cli", "headless-loopback"),
    ("qoder", "cli", "config-inventory"),
    ("qoder", "cli", "acp-stdio"),
    ("qoder", "cli", "cli-help"),
    ("qoder", "ide", "bundled-backend-rpc"),
    ("copilot", "cli", "prompt-mode"),
    ("copilot", "desktop", "desktop-bundled-prompt-mode"),
    ("lm-studio", "desktop", "bundled-preset-parser"),
    ("lm-studio", "desktop", "bundled-mcp-session"),
    ("lm-studio", "bionic", "bundled-bionic-probe"),
    ("lm-studio", "bionic", "session-model-picker"),
    ("lm-studio", "bionic", "session-local-inference"),
    ("lm-studio", "bionic", "session-local-mcp"),
    ("lm-studio", "bionic", "settings-mcp-https"),
    ("lm-studio", "bionic", "settings-skills"),
    ("lm-studio", "bionic", "settings-mcp-stdio"),
    ("lm-studio", "api", "loopback-http"),
}


@dataclass(frozen=True)
class ScopeTarget:
    product_id: str
    surface_id: str


@dataclass(frozen=True)
class RequestedItem:
    id: str
    label: str
    aliases: tuple[str, ...]
    targets: tuple[ScopeTarget, ...]


def _alias_key(value: object) -> str:
    text = _string(value, "Requested product alias")
    if text != text.strip():
        raise WeaverError("Requested product alias must not contain surrounding whitespace")
    return text.casefold()


def _load_catalog(root: Path) -> dict[str, Any]:
    data = _strict_object(
        load_json(Path(root) / "catalogs/requested-coverage.json"),
        "requested coverage catalog", {"schema_version", "requested_items"},
        {"runtime_observations"},
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise WeaverError("Requested coverage catalog: unsupported schema version")
    return data


def _load_scope(data: dict[str, Any], integrations: dict[str, IntegrationSpec]) -> tuple[RequestedItem, ...]:
    entries = data["requested_items"]
    if not isinstance(entries, list) or not entries:
        raise WeaverError("Requested coverage catalog: non-empty requested_items list required")
    items: list[RequestedItem] = []
    identifiers: set[str] = set()
    aliases: set[str] = set()
    for value in entries:
        entry = _strict_object(value, "requested item", {"id", "label", "aliases", "targets"})
        identifier = _canonical_id(entry["id"], "requested item id")
        if identifier in identifiers:
            raise WeaverError("Requested coverage catalog: duplicate requested item id")
        identifiers.add(identifier)
        label = _string(entry["label"], "requested item label")
        item_aliases = _string_list(entry["aliases"], "requested item aliases")
        if not item_aliases:
            raise WeaverError("Requested coverage catalog: explicit aliases required")
        target_data = entry["targets"]
        if not isinstance(target_data, list) or not target_data:
            raise WeaverError("Requested coverage catalog: explicit targets required")
        targets: list[ScopeTarget] = []
        for target_value in target_data:
            target = _strict_object(target_value, "requested target", {"product_id", "surface_id"})
            product = _canonical_id(target["product_id"], "requested product id")
            surface = _canonical_id(target["surface_id"], "requested surface id")
            if product not in integrations or surface not in integrations[product].surface_ids():
                raise WeaverError("Requested coverage catalog: target is absent from integration registry")
            parsed = ScopeTarget(product, surface)
            if parsed in targets:
                raise WeaverError("Requested coverage catalog: duplicate target")
            targets.append(parsed)
        products = {target.product_id for target in targets}
        for alias in item_aliases:
            key = _alias_key(alias)
            if key in aliases:
                raise WeaverError("Requested coverage catalog: alias collision")
            if key in integrations and products != {key}:
                raise WeaverError("Requested coverage catalog: alias shadows a canonical product id")
            aliases.add(key)
        items.append(RequestedItem(identifier, label, item_aliases, tuple(targets)))
    return tuple(items)


def load_requested_scope(root: Path) -> tuple[RequestedItem, ...]:
    """Load explicit mappings and reject missing references or ambiguous catalog aliases."""
    return _load_scope(_load_catalog(root), load_integrations(root))


def resolve_product_alias(root: Path, value: str) -> str:
    """Resolve one canonical product, refusing aliases for a group of products."""
    key = _alias_key(value)
    integrations = load_integrations(root)
    scope = _load_scope(_load_catalog(root), integrations)
    if key in integrations:
        return key
    for item in scope:
        if key in {_alias_key(alias) for alias in item.aliases}:
            products = {target.product_id for target in item.targets}
            if len(products) != 1:
                raise WeaverError("Requested alias names multiple products; inspect coverage targets")
            return next(iter(products))
    raise WeaverError("Unknown product alias; inspect inventory or coverage")


def _observation_field(value: object, pattern: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value, flags=re.ASCII):
        raise WeaverError(f"Runtime observation: invalid {label}")
    return value


def _load_observations(
    data: dict[str, Any], scope: tuple[RequestedItem, ...],
) -> dict[ScopeTarget, list[dict[str, Any]]]:
    entries = data.get("runtime_observations", [])
    if not isinstance(entries, list):
        raise WeaverError("Runtime observations must be a list")
    targets = {target for item in scope for target in item.targets}
    observations: dict[ScopeTarget, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, ...]] = set()
    for value in entries:
        row = _strict_object(value, "runtime observation", _OBSERVATION_FIELDS, {"extension_version"})
        product = _canonical_id(row["product_id"], "runtime observation product")
        surface = _canonical_id(row["surface_id"], "runtime observation surface")
        target = ScopeTarget(product, surface)
        if target not in targets:
            raise WeaverError("Runtime observation must reference a requested registry surface")
        checked_on = _observation_field(row["checked_on"], r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "date")
        try:
            date.fromisoformat(checked_on)
        except ValueError:
            raise WeaverError("Runtime observation: invalid date") from None
        os_id = _canonical_id(row["os_id"], "runtime observation OS")
        if os_id not in OS_IDS:
            raise WeaverError("Runtime observation: unknown OS")
        public_catalog = ((product, surface) in {("openrouter", "gateway"), ("kilo", "gateway")}
                          and row["interface"] in ("public-model-list", "public-provider-endpoint-list"))
        hosted_auth_boundary = (product == "perplexity" and surface in {"agent-api", "orchestrator"}
                                and row["interface"] == "unauthenticated-catalog-http")
        hosted_browser = (product, surface, row["interface"]) in {
            ("perplexity", "app", "public-web-ui"),
            ("chatgpt", "web", "public-web-ui"),
            ("chatgpt", "web", "hosted-chat-ui"),
            ("chatgpt", "work-cloud", "hosted-work-ui"),
        }
        product_version = row["product_version"]
        if not hosted_browser or product_version is not None:
            product_version = _observation_field(product_version, r"[0-9][A-Za-z0-9.+_-]{0,63}", "product version")
        engine_version = row["engine_version"]
        if not (public_catalog or hosted_auth_boundary) or engine_version is not None:
            engine_version = _observation_field(engine_version, r"[0-9][A-Za-z0-9.+_-]{0,63}", "engine version")
        identity = {
            "product_version": product_version,
            "engine_version": engine_version,
            "os_id": os_id,
            "os_variant": _observation_field(row["os_variant"], rf"{os_id}:[A-Za-z0-9][A-Za-z0-9.() _:-]{{0,95}}", "OS variant"),
            "architecture": _observation_field(row["architecture"], r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", "architecture"),
            "interface": _canonical_id(row["interface"], "runtime observation interface"),
            "environment": _canonical_id(row["environment"], "runtime observation environment"),
            "checked_on": checked_on,
            "source_commit": _observation_field(row["source_commit"], r"[0-9a-f]{40}", "source commit"),
        }
        environments = {
            ("openrouter", "gateway"): {"public-unauthenticated"},
            ("chatgpt", "web"): ({"public-unauthenticated"} if row["interface"] == "public-web-ui"
                                else {"existing-account-read-only"}),
            ("chatgpt", "work-cloud"): {"existing-account-read-only"},
            ("perplexity", "app"): {"public-unauthenticated"},
            ("perplexity", "agent-api"): {"public-unauthenticated"},
            ("perplexity", "orchestrator"): {"public-unauthenticated", "isolated-sdk-mock"},
            ("antigravity", "cli"): {"isolated-release-loopback", "isolated-signed-out"},
            ("qwen-code", "cli"): {"isolated-release-loopback"},
            ("qwen-code", "desktop"): {"isolated-release-loopback"},
            ("qwen-code", "vscode"): {"isolated-release-loopback"},
            ("opencode", "cli"): {"isolated-release-loopback", "isolated-local-inference"},
            ("kilo", "cli"): {"isolated-release-loopback", "isolated-local-inference"},
            ("kilo", "ide"): {"isolated-release-loopback"},
            ("kilo", "desktop"): {"isolated-release-loopback"},
            ("kilo", "gateway"): {"public-unauthenticated"},
            ("openhands", "cli"): {"isolated-release-loopback"},
            ("openhands", "canvas"): {"isolated-release-loopback"},
            ("cursor", "cli"): {"isolated-signed-out", "isolated-native-loader"},
            ("cursor", "ide"): {"isolated-native-loader"},
            ("jetbrains-air", "ide"): {"isolated-native-loader"},
            ("jetbrains-air", "desktop"): {"isolated-native-loader"},
            ("antigravity", "sdk"): {"isolated-native-loader"},
            ("kiro", "ide"): {"isolated-native-loader"},
            ("cline", "cli"): {"isolated-release-loopback", "isolated-local-inference"},
            ("cline", "desktop"): {"isolated-release-loopback", "isolated-local-inference"},
            ("cline", "ide"): {"isolated-release-loopback"},
            ("copilot", "desktop"): {"isolated-release-loopback"},
            ("factory", "cli"): {"isolated-release-loopback", "isolated-signed-out"},
            ("hermes", "cli"): {"isolated-release-loopback", "isolated-signed-out"},
            ("kimi-code", "cli"): {"isolated-release-loopback"},
            ("lm-studio", "desktop"): {"isolated-native-loader"},
            ("lm-studio", "bionic"): {"isolated-native-loader"},
            ("lm-studio", "api"): {"isolated-release-loopback"},
            ("claude-code", "vscode"): {"isolated-signed-out", "isolated-loopback"},
            ("claude-code", "cli"): {"isolated-signed-out", "isolated-loopback"},
            ("claude-code", "desktop"): {"isolated-loopback"},
        }.get((product, surface), {"isolated-signed-out"})
        if ((product, surface, identity["interface"]) not in _OBSERVATION_INTERFACES
                or identity["environment"] not in environments):
            raise WeaverError("Runtime observation: unsupported interface or environment")
        if "extension_version" in row:
            identity["extension_version"] = _observation_field(
                row["extension_version"], r"[0-9][A-Za-z0-9.+_-]{0,63}", "extension version")
        is_extension = (
            identity["interface"] in {
                "bundled-runtime", "bundled-extension-loaders", "vscode-extension-host", "vscode-extension-mcp-approval",
            }
            or (product, surface) == ("codex", "ide")
        )
        if is_extension != ("extension_version" in identity):
            raise WeaverError("Runtime observation: extension version must match the extension runtime scope")
        checks = row["checks"]
        if not isinstance(checks, list) or not checks:
            raise WeaverError("Runtime observation requires explicit checks")
        parsed_checks = []
        for entry in checks:
            check = _strict_object(entry, "runtime observation check", {"check", "evidence_sha256"})
            name = _canonical_id(check["check"], "runtime observation check")
            if name not in _OBSERVATION_CHECKS:
                raise WeaverError("Runtime observation: unknown check")
            digest = _observation_field(check["evidence_sha256"], r"[0-9a-f]{64}", "evidence hash")
            key = (product, surface, *identity.values(), name)
            if key in seen:
                raise WeaverError("Duplicate runtime observation check")
            seen.add(key)
            capability, outcome = _OBSERVATION_CHECKS[name]
            parsed_checks.append({"check": name, "capability": capability, "outcome": outcome, "evidence_sha256": digest})
        observations[target].append({**identity, "checks": parsed_checks})
    return observations


def coverage_report(root: Path) -> dict[str, Any]:
    """Describe mapping and evidence gaps without treating catalog presence as support."""
    integrations = load_integrations(root)
    data = _load_catalog(root)
    scope = _load_scope(data, integrations)
    observations = _load_observations(data, scope)
    rows: list[dict[str, Any]] = []
    referenced: set[ScopeTarget] = set()
    states: dict[str, int] = {}
    pending_runtime = 0
    missing_documentation = 0
    for item in scope:
        targets: list[dict[str, Any]] = []
        for target in item.targets:
            product = integrations[target.product_id]
            surface = product.surface(target.surface_id)
            capabilities: dict[str, Any] = {}
            for name in CAPABILITY_NAMES:
                evidence = surface.capabilities[name]
                capabilities[name] = {
                    "state": evidence.state,
                    "sources": list(evidence.sources),
                    "checked_on": evidence.checked_on,
                    "version_constraint": evidence.version_constraint,
                    "documentation_status": "recorded" if evidence.sources else "missing",
                    "fixture_tests": list(evidence.fixture_tests),
                    "fixture_status": "recorded" if evidence.fixture_tests else "pending",
                    "runtime_checks": list(evidence.runtime_checks),
                    "runtime_status": "recorded" if evidence.runtime_checks else "pending",
                }
                if target not in referenced:
                    states[evidence.state] = states.get(evidence.state, 0) + 1
                    pending_runtime += not bool(evidence.runtime_checks)
                    missing_documentation += not bool(evidence.sources)
            targets.append({
                "product_id": target.product_id, "product_name": product.name,
                "kind": product.kind, "surface_id": target.surface_id,
                "config_scope": surface.config_scope, "capabilities": capabilities,
                "runtime_observations": observations.get(target, []),
            })
            referenced.add(target)
        rows.append({
            "id": item.id, "label": item.label, "aliases": list(item.aliases),
            "disposition": "mapped", "targets": targets,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "requested_items": rows,
        "summary": {
            "requested_items": len(rows), "mapped_items": len(rows),
            "referenced_surfaces": len(referenced), "capability_states": states,
            "missing_documentation_capabilities": missing_documentation,
            "pending_runtime_capabilities": pending_runtime,
            "surfaces_with_runtime_observations": len(observations),
            "runtime_observation_checks": sum(len(record["checks"]) for records in observations.values()
                                              for record in records),
        },
        "limitations": list(LIMITATIONS),
    }
