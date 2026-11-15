"""Stage exact text-chat provider selections as classic LM Studio model plugins."""

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters import json_bytes
from .artifacts import RenderedArtifact
from .config import WeaverError

PROTOCOLS = {"openrouter": "openrouter-chat", "openai": "openai-chat", "lm-studio": "openai-chat"}
SOURCE = "https://lmstudio.ai/docs/typescript/plugins/generator"
PLUGIN_EXAMPLE = "https://lmstudio.ai/lmstudio/openai-compat-endpoint"

_GENERATOR = '''import { createConfigSchematics, type Chat, type GeneratorController, type PluginContext } from "@lmstudio/sdk";

const endpoint = ENDPOINT;
const selection = SELECTION;
const requiresToken = REQUIRES_TOKEN;
const credentials = createConfigSchematics()
  .field("apiToken", "string", { displayName: "Provider API token", isProtected: true }, "")
  .build();

function textMessages(history: Chat) {
  return Array.from(history, message => {
    const role = message.getRole();
    if (role === "tool" || message.getToolCallRequests().length || message.hasFiles()) {
      throw new Error("This generator accepts text chat without tool history or attachments.");
    }
    return { role, content: message.getText() };
  });
}

async function generate(controller: GeneratorController, history: Chat) {
  controller.abortSignal.throwIfAborted();
  const token = controller.getGlobalPluginConfig(credentials).get("apiToken").trim();
  if (requiresToken && !token) {
    throw new Error("Set the provider token in the generator's global configuration.");
  }
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = "Bearer " + token;
  const response = await fetch(endpoint, {
    method: "POST",
    headers,
    signal: controller.abortSignal,
    redirect: "error",
    body: JSON.stringify({ ...selection, messages: textMessages(history), stream: false }),
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error(`Chat Completions request failed with HTTP ${response.status}.`);
  }
  const data = await response.json();
  controller.abortSignal.throwIfAborted();
  const choice = data?.choices?.[0];
  if (choice?.finish_reason === "tool_calls" || choice?.message?.tool_calls?.length) {
    throw new Error("The provider returned a tool call to a text-only generator.");
  }
  const message = choice?.message;
  const text = message?.content || message?.refusal;
  if (typeof text !== "string" || !text) throw new Error("The provider returned no visible text.");
  controller.fragmentGenerated(text);
}

export async function main(context: PluginContext) {
  context.withGlobalConfigSchematics(credentials);
  context.withGenerator(generate);
}
'''


def render_lmstudio_generator(
    root: Path, provider_id: str, model_id: str, snapshot: dict[str, Any] | None = None, *,
    protocol: str | None = None, effort: str | None = None, budget: int | None = None,
    reasoning_enabled: bool | None = None,
) -> list[RenderedArtifact]:
    from .models import check_model, load_model_providers

    if provider_id not in PROTOCOLS or protocol not in (None, PROTOCOLS[provider_id]):
        raise WeaverError("LM Studio Generator staging requires OpenRouter, OpenAI, or LM Studio Chat Completions")
    checked = check_model(root, provider_id, model_id, snapshot, protocol=PROTOCOLS[provider_id], effort=effort,
                          budget_tokens=budget, reasoning_enabled=reasoning_enabled)
    if snapshot is not None and not checked["visible_in_snapshot"]:
        raise WeaverError("The saved provider catalog does not list the selected model")
    output = (checked["architecture"] or {}).get("output_modalities")
    if output is not None and "text" not in output:
        raise WeaverError("The LM Studio Generator requires a model that produces text")
    parameters = checked["supported_parameters"]
    if (provider_id == "openrouter" and checked["reasoning_payload"]
            and parameters is not None and "reasoning" not in parameters):
        raise WeaverError("The saved OpenRouter model does not advertise the reasoning parameter")
    base = load_model_providers(root)[provider_id]["base_url"]
    if provider_id == "lm-studio":
        base += "/v1"
    endpoint = base + "/chat/completions"
    selection = {"model": checked["model_id"], **checked["reasoning_payload"]}
    identity = hashlib.sha256(json_bytes({"endpoint": endpoint, "request": selection})).hexdigest()[:12]
    name = f"{provider_id}-{identity}"
    prefix = PurePosixPath("lm-studio", "generator", name)
    source = (_GENERATOR.replace("ENDPOINT;", json.dumps(endpoint) + ";", 1)
              .replace("REQUIRES_TOKEN;", json.dumps(provider_id != "lm-studio") + ";", 1)
              .replace("SELECTION;", json.dumps(selection, indent=2) + ";", 1))
    docs = [
        "# LM Studio model generator", "",
        f"Model `{model_id}` via `{endpoint}`.",
        "Review `manifest.json` and `src/index.ts`. From the plugin directory, install the pinned SDK "
        "dependency and use LM Studio's documented `lms dev` workflow when you choose to activate the generator.",
        f"Select `weaver-local/{name}` in the classic Desktop model dropdown.",
        "Enter any required token in the generator's protected global configuration field.",
        "The selected chat's text history goes to the listed endpoint. Host and provider policy govern usage.",
        "This adapter returns visible text from a single non-streaming Chat Completions request. "
        "The staged reasoning controls are sent to the provider. Attachments, tool calls, and "
        "reasoning-trace display require a separate generator implementation.",
        "Plugin activation and provider inference require verification in the intended host.", "",
        f"- {SOURCE}",
        f"- {PLUGIN_EXAMPLE}",
        "",
    ]
    return [
        RenderedArtifact(prefix / "manifest.json",
                         json_bytes({"type": "plugin", "runner": "node", "owner": "weaver-local",
                                     "name": name, "revision": 1}), "plugin-manifest"),
        RenderedArtifact(prefix / "package.json",
                         json_bytes({"name": f"weaver-lms-{name}", "version": "1.0.0", "private": True, "type": "module",
                                     "dependencies": {"@lmstudio/sdk": "2.1.0"}}), "plugin-dependencies"),
        RenderedArtifact(prefix / "src/index.ts", source.encode(), "plugin-source"),
        RenderedArtifact(prefix / "SETUP.md", "\n".join(docs).encode(), "model-setup"),
    ]
