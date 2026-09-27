/**
 * GET /api/models/all
 *
 * Unified model catalogue for the AgentChat model picker.
 *
 * Two runtime groups — each routes through a completely different execution path:
 *
 * 1. "GitHub Copilot SDK" group  (model.runtime = "copilot")
 *    What it is: The GitHub Copilot SDK (`github-copilot-sdk` Python package).
 *                NOT CopilotKit (the React UI library). NOT just an LLM call.
 *    What it does: Launches a `CopilotClient` subprocess running `gh copilot` CLI
 *                  in autopilot mode. Has native tool-calling (shell, file r/w,
 *                  Python scripts). Reads AGENTS.md from the agent workspace.
 *                  Tool events (tool_start/tool_end) stream back to the UI via SSE hooks.
 *    Route: /api/agent/chat?mode=copilot → gateway /copilot/chat → CopilotClient
 *    Auth:  GITHUB_TOKEN (api.githubcopilot.com) or BYOK via LiteLLM
 *    Models: claude-sonnet-4.5, gpt-5.5, gemini-3.1-pro-preview, etc.
 *            (live list from /copilot/models if GITHUB_TOKEN is set; fallback list otherwise)
 *
 * 2. "LiteLLM (tier routing)" group  (model.runtime = "litellm")
 *    What it is: A direct OpenAI-compatible streaming call to gateway /v1 (litellm SDK).
 *                No orchestration, no tool calling, no agent workspace.
 *    What it does: Raw /chat/completions call to LiteLLM; response streamed to UI.
 *    Route: /api/agent/chat?mode=litellm → LiteLLM /chat/completions
 *    Models: tier1/2/3 aliases (see infra/litellm/config.yaml for current backend routing)
 *    Current routing: all tiers → Gemini 2.5 Flash variants (GEMINI_API_KEY)
 *
 * Why no copilot/* aliases in the LiteLLM group?
 *    LiteLLM does have copilot/* entries (e.g. copilot/claude-sonnet-4.5) but these
 *    are not shown in the UI — the same models are accessible directly via the
 *    Copilot SDK group, and duplicating them would cause confusion about which
 *    execution path is active.
 */

import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, UNAUTHENTICATED } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export type ModelRuntime = "copilot" | "litellm";

export interface UnifiedModel {
  id: string;
  label: string;
  runtime: ModelRuntime;
  group: string;
  /** True only for GitHub Copilot SDK models where the subscription plan
   *  allows the user to switch models.  False means the CLI ignores the
   *  model selection and always uses its internal default (currently
   *  claude-sonnet-4.6). Use BYOK (LiteLLM models) to switch models. */
  model_picker_enabled?: boolean;
  /** Real context-window size (tokens) for this model, sourced dynamically
   *  from the gateway's capability map.  Lets the UI show an accurate
   *  context-usage ring and auto-compact threshold per model — including
   *  after a mid-chat model switch.  Undefined when unknown (UI falls back
   *  to a static estimate). */
  contextWindow?: number;
}

export interface UnifiedModelsResponse {
  models: UnifiedModel[];
  source: string;
  configured_providers: string[];
}

const COPILOT_FALLBACK: { id: string; label: string; context_window?: number }[] = [
  { id: "auto", label: "auto (SDK picks)", context_window: 200_000 },
  { id: "claude-sonnet-4.5", label: "Claude Sonnet 4.5", context_window: 200_000 },
  { id: "claude-haiku-4.5", label: "Claude Haiku 4.5", context_window: 200_000 },
  { id: "gpt-5.5", label: "GPT 5.5", context_window: 400_000 },
  { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro", context_window: 1_000_000 },
];

// LiteLLM model catalogue.
// `provider` maps to an env-var gate — the model is only shown when that provider key is set.
// `provider: null` = always shown (tier aliases route to whatever is configured).
const LITELLM_MODELS: { id: string; label: string; group: string; provider: string | null }[] = [
  // Tiers — always shown; they're routing aliases, not pinned to a single provider key
  { id: "tier1-local-qwen3", label: "Tier 1 (fast / cheap)",   group: "LiteLLM — Tiers",       provider: null },
  { id: "tier2-sonnet",      label: "Tier 2 (balanced)",        group: "LiteLLM — Tiers",       provider: null },
  { id: "tier3-opus",        label: "Tier 3 (powerful)",        group: "LiteLLM — Tiers",       provider: null },
  // Google Gemini — requires GEMINI_API_KEY
  { id: "gemini/gemini-2.5-pro",        label: "Gemini 2.5 Pro",        group: "LiteLLM — Gemini",     provider: "gemini" },
  { id: "gemini/gemini-2.5-flash",      label: "Gemini 2.5 Flash",      group: "LiteLLM — Gemini",     provider: "gemini" },
  { id: "gemini/gemini-2.5-flash-lite", label: "Gemini 2.5 Flash Lite", group: "LiteLLM — Gemini",     provider: "gemini" },
  // Anthropic — requires ANTHROPIC_API_KEY
  { id: "anthropic/claude-opus-4-5",   label: "Claude Opus 4.5",   group: "LiteLLM — Anthropic", provider: "anthropic" },
  { id: "anthropic/claude-sonnet-4-5", label: "Claude Sonnet 4.5", group: "LiteLLM — Anthropic", provider: "anthropic" },
  { id: "anthropic/claude-haiku-4-5",  label: "Claude Haiku 4.5",  group: "LiteLLM — Anthropic", provider: "anthropic" },
  // OpenRouter — requires OPENROUTER_API_KEY
  { id: "openrouter/anthropic/claude-sonnet-4-5", label: "Claude Sonnet 4.5", group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/openai/gpt-4o",               label: "GPT-4o",            group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/google/gemini-2.5-pro",        label: "Gemini 2.5 Pro",    group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/google/gemini-2.5-flash",      label: "Gemini 2.5 Flash",  group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/meta-llama/llama-4-maverick",  label: "Llama 4 Maverick",  group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/deepseek/deepseek-r1",            label: "DeepSeek R1 (reasoning)",          group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/deepseek/deepseek-r1-0528",        label: "DeepSeek R1 0528 (reasoning)",      group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/deepseek/deepseek-chat-v3-0324",  label: "DeepSeek V3 0324",                  group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/deepseek/deepseek-chat",           label: "DeepSeek (latest alias)",           group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/deepseek/deepseek-v4-pro",         label: "DeepSeek V4 Pro (1M ctx, agentic)", group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/deepseek/deepseek-v4-flash",       label: "DeepSeek V4 Flash (fast)",          group: "LiteLLM — OpenRouter", provider: "openrouter" },
  // Qwen (Alibaba) — latest 2026 models
  { id: "openrouter/qwen/qwen3.7-max",                 label: "Qwen 3.7 Max (flagship, agentic)",  group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/qwen/qwen3.7-plus",                label: "Qwen 3.7 Plus (cost-effective)",    group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/qwen/qwen3.6-plus",                label: "Qwen 3.6 Plus (coding+agentic)",    group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/qwen/qwen3.5-flash-02-23",         label: "Qwen 3.5 Flash (fast/cheap)",       group: "LiteLLM — OpenRouter", provider: "openrouter" },
  // Kimi (MoonshotAI)
  { id: "openrouter/moonshotai/kimi-k2.6",             label: "Kimi K2.6 (multi-agent)",          group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/moonshotai/kimi-k2-thinking",      label: "Kimi K2 Thinking (reasoning)",     group: "LiteLLM — OpenRouter", provider: "openrouter" },
  { id: "openrouter/moonshotai/kimi-k2.5",             label: "Kimi K2.5 (visual+coding)",        group: "LiteLLM — OpenRouter", provider: "openrouter" },
  // OpenAI — requires OPENAI_API_KEY
  { id: "openai/gpt-4o",     label: "GPT-4o",      group: "LiteLLM — OpenAI",  provider: "openai" },
  { id: "openai/gpt-4o-mini", label: "GPT-4o Mini", group: "LiteLLM — OpenAI",  provider: "openai" },
  { id: "openai/o3-mini",    label: "o3-mini",     group: "LiteLLM — OpenAI",  provider: "openai" },
  // GitHub Copilot via litellm SDK — requires GITHUB_TOKEN
  { id: "copilot/gpt-4o",        label: "GPT-4o (Copilot)",        group: "LiteLLM — Copilot", provider: "github" },
  { id: "copilot/claude-sonnet", label: "Claude Sonnet (Copilot)",  group: "LiteLLM — Copilot", provider: "github" },
  { id: "copilot/o3-mini",       label: "o3-mini (Copilot)",        group: "LiteLLM — Copilot", provider: "github" },
];

/** Speech-to-text models (Deepgram, AssemblyAI, Whisper, …) are enabled via
 *  Settings → Models to power the Note Taker's STT tier — they take audio in
 *  and return a transcript, not a chat completion, so they can't drive
 *  AgentChat or a Custom App. Mirrors the gateway's own
 *  `_is_transcription_model` heuristic (routes/settings.py) so a model
 *  enabled for STT never leaks into the chat/App-Workshop picker. */
function isVoiceOnlyModel(id: string, provider: string): boolean {
  const mid = id.toLowerCase();
  return (
    provider === "deepgram" ||
    provider === "assemblyai" ||
    mid.startsWith("deepgram/") ||
    mid.startsWith("assemblyai/") ||
    mid.includes("whisper") ||
    mid.includes("transcribe") ||
    /\/nova-/.test(mid)
  );
}

export async function GET(): Promise<
  NextResponse<UnifiedModelsResponse | typeof UNAUTHENTICATED>
> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  // ── Get live provider status from the gateway ─────────────────────────────
  // The gateway writes keys to os.environ immediately when saved, so it always
  // has up-to-date knowledge of which providers are configured.  Next.js
  // process.env is only populated at startup and goes stale after keys are
  // added via the Settings page — never use process.env for this check.
  const configured = new Set<string>();
  let gatewayReachable = false;
  // True while the Console Router serves chat. The Router serves TIERS and
  // refuses a bare model id by design (D32.7), so under it a raw model in
  // this list is a guaranteed 400. Measured on production, 2026-09-24:
  // `openrouter/qwen/qwen3.7-max` was picked and refused `tier_unknown`.
  let routerServing = false;
  try {
    const provRes = await fetch(`${GATEWAY_URL}/settings/llm`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(3_000),
    });
    if (provRes.ok) {
      const data = (await provRes.json()) as {
        providers?: { id: string; configured: boolean }[];
        router_serving?: boolean;
      };
      routerServing = data.router_serving === true;
      if (Array.isArray(data.providers)) {
        for (const p of data.providers) {
          if (p.configured) configured.add(p.id);
        }
        gatewayReachable = true;
      }
    }
  } catch (_e) {
    // Gateway unreachable — fall back to process.env so the picker still works
  }

  // Fallback: read process.env when gateway is unreachable (dev / cold start)
  if (!gatewayReachable) {
    if (process.env.GEMINI_API_KEY?.trim())      configured.add("gemini");
    if (process.env.ANTHROPIC_API_KEY?.trim())   configured.add("anthropic");
    if (process.env.OPENROUTER_API_KEY?.trim())  configured.add("openrouter");
    if (process.env.OPENAI_API_KEY?.trim())      configured.add("openai");
    if (process.env.GITHUB_TOKEN?.trim())        configured.add("github");
    if (process.env.GROQ_API_KEY?.trim())        configured.add("groq");
    if (process.env.MISTRAL_API_KEY?.trim())     configured.add("mistral");
    if (process.env.TOGETHER_API_KEY?.trim())    configured.add("together");
    if (process.env.VLLM_BASE_URL?.trim())       configured.add("vllm");
  }

  // ── Fetch enabled models + hidden list from the gateway ─────────────────
  // Stored in infra/custom_models.json.  "enabled" = models turned on via
  // Settings → Models eye icon.  "hidden" = legacy suppression list.
  let enabledModels: { id: string; label: string; provider: string; group: string }[] = [];
  const hiddenSet = new Set<string>();
  try {
    const cr = await fetch(`${GATEWAY_URL}/settings/llm/enabled-models`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(3_000),
    });
    if (cr.ok) {
      const data = (await cr.json()) as
        | { enabled?: { id: string; label: string; provider: string; group: string }[];
            custom?: { id: string; label: string; provider: string; group: string }[];
            hidden?: string[] }
        | { id: string; label: string; provider: string; group: string }[];
      if (Array.isArray(data)) {
        enabledModels = data;
      } else {
        // Prefer "enabled", fall back to legacy "custom" key
        enabledModels = (data as { enabled?: typeof enabledModels; custom?: typeof enabledModels }).enabled
          ?? (data as { custom?: typeof enabledModels }).custom
          ?? [];
        for (const id of (data as { hidden?: string[] }).hidden ?? []) hiddenSet.add(id);
      }
    }
  } catch (_e) {
    // safe to ignore — custom_models.json may not exist yet
  }
  // Fetched live from the gateway when GITHUB_TOKEN is set; fallback otherwise.
  type CopilotModel = { id: string; label: string; model_picker_enabled?: boolean; context_window?: number };
  let copilotModels: CopilotModel[] = configured.has("github")
    ? COPILOT_FALLBACK
    : [{ id: "auto", label: "auto (SDK picks — needs GITHUB_TOKEN)" }];
  let source = configured.has("github") ? "fallback" : "no-token";

  if (configured.has("github")) {
    try {
      const res = await fetch(`${GATEWAY_URL}/copilot/models`, {
        signal: AbortSignal.timeout(4000),
      });
      if (res.ok) {
        const data = (await res.json()) as {
          models?: CopilotModel[];
          source?: string;
        };
        if (Array.isArray(data.models) && data.models.length > 0) {
          copilotModels = [
            // "auto" lets the SDK pick — default to the Claude Sonnet context
            // window (the SDK's current default model).
            { id: "auto", label: "auto (SDK picks)", model_picker_enabled: false, context_window: 200_000 },
            ...data.models.filter((m) => m.id !== "auto"),
          ];
          source = data.source ?? "live";
        }
      }
    } catch (_e) {
      // keep fallback list
    }
  }

  // ── Build model list ─────────────────────────────────────────────────────
  //
  // The picker contains exactly three categories:
  //
  //   1. Tier aliases  — tier1/tier2/tier3, always shown.  These are routing
  //      shortcuts that go through whatever provider is configured on the
  //      gateway, not pinned models.  They work even with no models enabled.
  //
  //   2. GitHub Copilot SDK models  — shown only when GITHUB_TOKEN is set.
  //      These use a completely different execution path (Copilot CLI) and
  //      are not LiteLLM models.
  //
  //   3. User-enabled models  — exactly the models the user turned on via
  //      Settings → Models → eye icon (stored in infra/enabled_models.json).
  //      Nothing else.  The LITELLM_MODELS built-in list is only used as a
  //      display-label lookup — it never adds models to the picker on its own.
  //      Speech-to-text models (Deepgram, AssemblyAI, Whisper) can be enabled
  //      here too — for the Note Taker's STT tier, not chat — so this group
  //      is filtered through isVoiceOnlyModel() before it reaches the picker.
  //
  const providerLabel: Record<string, string> = {
    gemini: "Gemini", openai: "OpenAI", anthropic: "Anthropic",
    openrouter: "OpenRouter", github: "GitHub Copilot", groq: "Groq",
    deepseek: "DeepSeek", mistral: "Mistral", together: "Together AI",
    ollama: "Ollama", vllm: "vLLM",
  };

  // Tier routing aliases — always present regardless of what's enabled.
  const tierModels = LITELLM_MODELS.filter((m) => m.provider === null);

  // The Models-page allow-list: every model id the user explicitly enabled
  // (LiteLLM + GitHub Copilot).  Used to gate the Copilot SDK group so the
  // picker shows only what the user turned on (strict allow-list), instead of
  // the full live Copilot catalogue.
  const enabledIdSet = new Set(enabledModels.map((m) => m.id));

  // Resolve label/group for an enabled model: prefer the built-in LITELLM_MODELS
  // entry for accurate labels, fall back to what's stored in enabled_models.json.
  const resolveModel = (em: { id: string; label: string; provider: string }) => {
    const builtin = LITELLM_MODELS.find((b) => b.id === em.id);
    return {
      id: em.id,
      label: builtin?.label ?? em.label,
      runtime: "litellm" as ModelRuntime,
      group: builtin?.group ?? providerLabel[em.provider] ?? em.provider,
    };
  };

  const models: UnifiedModel[] = [
    // ① Tier routing aliases — always present
    ...tierModels.map((m) => ({
      id: m.id, label: m.label, runtime: "litellm" as ModelRuntime, group: m.group,
    })),

    // ② GitHub Copilot SDK group — separate execution path, shown when token is set.
    //    Strict allow-list: only "auto" (the safe default picker) plus Copilot
    //    models the user enabled on the Models page.  IDs match because
    //    /llm/provider-models?provider=github now serves the same live Copilot
    //    list this dropdown reads from.
    ...(configured.has("github")
      ? copilotModels
          .filter((m) => !hiddenSet.has(m.id)
            && (m.id === "auto" || enabledIdSet.has(m.id)))
          .map((m) => ({
            id: m.id,
            label: m.label,
            runtime: "copilot" as ModelRuntime,
            group: "GitHub Copilot SDK",
            model_picker_enabled: m.model_picker_enabled ?? false,
            // Real context window from the Copilot SDK (provider-sourced) when
            // available.
            ...(m.context_window && m.context_window > 0
              ? { contextWindow: m.context_window }
              : {}),
          }))
      : []),

    // ③ User-enabled models — the only source for LiteLLM models.
    //    If the user hasn't enabled anything yet, nothing extra appears here
    //    (the tier aliases above are always sufficient to start chatting).
    //    GitHub-provider models are Copilot-runtime and already surface in the
    //    Copilot group above (different execution path), so exclude them here to
    //    avoid litellm-runtime duplicates pointing at the wrong endpoint.
    //    ⚠️ NONE while the Router serves: each of these is a raw model id,
    //    and the Router answers a raw model with 400 (D32.7).
    ...(routerServing ? [] : enabledModels)
      .filter((m) => !hiddenSet.has(m.id)
        && !tierModels.some((t) => t.id === m.id)
        && m.provider !== "github"
        && !isVoiceOnlyModel(m.id, m.provider))
      .map(resolveModel),
  ];

  // Deduplicate by id (in case both the enabled list and Copilot list have the same id)
  const seen = new Set<string>();
  const deduped = models.filter((m) => {
    if (seen.has(m.id)) return false;
    seen.add(m.id);
    return true;
  });

  // ── Attach real context-window sizes (dynamic, per-model) ──────────────────
  // Fetch the gateway's capability map so the context-usage ring reflects the
  // actual model — critical when the user switches models mid-chat.
  let ctxMap: Record<string, number> = {};
  try {
    const cwRes = await fetch(`${GATEWAY_URL}/settings/llm/context-windows`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(3_000),
    });
    if (cwRes.ok) ctxMap = (await cwRes.json()) as Record<string, number>;
  } catch (_e) {
    // Gateway unreachable — leave contextWindow undefined; UI falls back to its
    // static estimate table.
  }
  const resolveCtx = (id: string): number | undefined =>
    ctxMap[id] ?? ctxMap[id.split("/").pop() ?? id] ?? undefined;
  for (const m of deduped) {
    const cw = resolveCtx(m.id);
    if (cw && cw > 0) m.contextWindow = cw;
  }

  return NextResponse.json({
    models: deduped,
    source,
    configured_providers: Array.from(configured),
  });
}
