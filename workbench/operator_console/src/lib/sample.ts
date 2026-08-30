// The designed placeholder — what every screen shows while its backend is owed.
//
// 🔴 **This file must NEVER be imported by anything under `src/app/`.**
// `source.test.ts` scans for it and fails the build. Sample data reaches a page
// through `read.ts` alone, because `read.ts` is the one place that stamps
// `origin: "sample"` onto it, and the origin is what makes the banner appear.
// An import that skipped that seam would put unlabelled fiction on an operator
// screen, which is the exact failure the whole seam exists to prevent.
//
// ⚠️ **The shapes here are DELIBERATELY unhealthy.** A sample catalog where
// everything works is useless for design: the warning states are the ones that
// need the most care, and a designer who never sees them ships them untested.
// So this holds a chain with no backup, a chain that is all one provider, a
// chain pointing at a model with no key, and a vendor whose only key was
// removed. Unhealthy, though — never IMPOSSIBLE. See the note on ACCOUNTS.
//
// ⚠️ **Prices are the real published ones as of 2026-08.** Invented numbers get
// quoted in a meeting. These are checkable.

import type {
  AiCatalog,
  CatalogModel,
  FailoverEvent,
  ModelRate,
  ProviderAccount,
  Task,
  Tier,
} from "./contract";

const TASKS: Task[] = [
  { slug: "chat", label: "Answer questions", natural_unit: "1k tokens" },
  { slug: "image", label: "Make an image", natural_unit: "image" },
  { slug: "transcribe", label: "Write down speech", natural_unit: "minute" },
  { slug: "speak", label: "Read text aloud", natural_unit: "1k characters" },
  { slug: "embed", label: "Build a search index", natural_unit: "1k tokens" },
];

const M = (
  id: string,
  label: string,
  kinds: CatalogModel["kinds"],
  ctx: number | null,
  out: number | null,
  inP: number | null,
  outP: number | null,
  description: string,
  declared = true,
  priced = true,
): CatalogModel => ({
  id,
  label,
  provider: id.split("/")[0],
  kinds,
  contextWindow: ctx,
  maxOutput: out,
  inputPer1M: inP,
  outputPer1M: outP,
  // ⚠️ The cache-read rate is deliberately UNTOLD for most sample rows —
  // that is the common real state, and the cost column must show what it
  // does about it.
  cachedInputPer1M: id === "anthropic/claude-sonnet-4" ? 0.3
    : id === "openai/gpt-4o" ? 1.25 : null,
  description,
  declared,
  priced,
});

const MODELS: CatalogModel[] = [
  M("anthropic/claude-sonnet-4", "Claude Sonnet 4", ["chat", "vision", "reasoning"],
    200000, 64000, 3, 15,
    "The everyday workhorse. Reads images, follows long instructions, fast enough for chat."),
  M("anthropic/claude-haiku-4", "Claude Haiku 4", ["chat", "vision"],
    200000, 32000, 0.8, 4,
    "Cheap and quick. Good for short answers and classification, weaker on long reasoning."),
  M("anthropic/claude-opus-4", "Claude Opus 4", ["chat", "vision", "reasoning"],
    200000, 32000, 15, 75,
    "The strongest and the most expensive. Worth it for hard analysis, wasteful for chat.",
    true, false),
  M("openai/gpt-4o", "GPT-4o", ["chat", "vision"],
    128000, 16384, 2.5, 10,
    "Broad general model. Reads images and is widely tested."),
  M("openai/gpt-4o-mini", "GPT-4o mini", ["chat", "vision"],
    128000, 16384, 0.15, 0.6,
    "The cheapest capable chat model here. A sensible last resort in a chain."),
  M("openai/o3-mini", "o3-mini", ["chat", "reasoning"],
    200000, 100000, 1.1, 4.4,
    "Thinks before answering. Slow, strong at maths and multi-step logic. No images."),
  M("openai/gpt-image-1", "GPT Image 1", ["image"],
    null, null, null, null,
    "Makes images from a description. Billed per image, not per token."),
  M("openai/whisper-1", "Whisper", ["transcribe"],
    null, null, null, null,
    "Speech to text. Solid on clean audio, no speaker names.",
    true, false),
  M("openai/tts-1", "OpenAI TTS", ["speak"],
    null, null, null, null,
    "Reads text aloud. Billed per character."),
  M("openai/text-embedding-3-large", "Embedding 3 Large", ["embed"],
    8191, null, 0.13, null,
    "Turns text into vectors for search. Not a chat model."),
  M("gemini/gemini-2.5-pro", "Gemini 2.5 Pro", ["chat", "vision", "reasoning"],
    1048576, 65536, 1.25, 10,
    "A million-token window. The one to reach for when the input is a whole repository."),
  M("gemini/gemini-2.5-flash", "Gemini 2.5 Flash", ["chat", "vision"],
    1048576, 65536, 0.3, 2.5,
    "Very cheap for the window it carries. A strong default backup."),
  M("groq/llama-3.3-70b", "Llama 3.3 70B", ["chat"],
    128000, 32768, 0.59, 0.79,
    "Open weights, served fast. No image reading."),
  M("groq/whisper-large-v3-turbo", "Whisper Large v3 Turbo", ["transcribe"],
    null, null, null, null,
    "Transcription at several times real time. Billed per minute of audio.",
    true, false),
  M("deepseek/deepseek-chat", "DeepSeek V3", ["chat"],
    64000, 8192, 0.27, 1.1,
    "Very cheap per token. Good general chat, no images."),
  M("deepseek/deepseek-reasoner", "DeepSeek R1", ["chat", "reasoning"],
    64000, 8192, 0.55, 2.19,
    "Shows its working. Cheap for a reasoning model.",
    false, false),
  M("assemblyai/universal-2", "AssemblyAI Universal 2", ["transcribe"],
    null, null, null, null,
    "Names the speakers, handles Hindi and English in one recording.",
    true, false),
  M("elevenlabs/eleven-turbo-v2", "ElevenLabs Turbo v2", ["speak"],
    null, null, null, null,
    "The most natural voices here. Billed per character.",
    false, false),
];

// ⚠️ **Every row here is a state the DATABASE allows.** An earlier version
// held two live platform keys for anthropic ("Main" and "Overflow") and
// probed health values ("answering", "rate limited twice in the last hour").
// The first is refused by `provider_credential_live_uniq` — proved against
// the real schema on 2026-08-30 — and the second is measured by nothing. A
// designed placeholder may be unhealthy; it must never be impossible,
// because the owner reads it to learn what the page means.
//
// `health: "unknown"` throughout, which is what `accountsFromWire` stamps on
// every real row until a probe exists.
const ACCOUNTS: ProviderAccount[] = [
  {
    id: "pa-1", provider: "anthropic", label: "Main billing account",
    apiBase: null, orgSlug: null,
    createdAt: "2026-07-14T09:12:00Z", revokedAt: null,
    health: "unknown", lastCheckedAt: null, healthNote: null,
  },
  {
    id: "pa-3", provider: "openai", label: "Platform account",
    apiBase: null, orgSlug: null,
    createdAt: "2026-07-14T09:15:00Z", revokedAt: null,
    health: "unknown", lastCheckedAt: null, healthNote: null,
  },
  {
    // ⚠️ `gemini`, not `google`. The Router resolves a vendor as the first
    // path segment of the model id, and litellm's id for Gemini is `gemini`.
    // Sample data that models the wrong slug teaches the wrong slug.
    id: "pa-4", provider: "gemini", label: null,
    apiBase: null, orgSlug: null,
    createdAt: "2026-08-20T15:02:00Z", revokedAt: null,
    health: "unknown", lastCheckedAt: null, healthNote: null,
  },
  {
    id: "pa-5", provider: "groq", label: "Fast transcription",
    apiBase: null, orgSlug: null,
    createdAt: "2026-08-21T08:30:00Z", revokedAt: null,
    health: "unknown", lastCheckedAt: null, healthNote: null,
  },
  {
    // BYOK: one customer's own account, beside our platform key for the
    // same vendor. Legal, and the state the demoted row exists to draw.
    id: "pa-6", provider: "openai", label: "Fracktal's own key",
    apiBase: null, orgSlug: "fracktal",
    createdAt: "2026-08-05T12:00:00Z", revokedAt: null,
    health: "unknown", lastCheckedAt: null, healthNote: null,
  },
  {
    // A vendor whose ONLY key was removed — the "dropped" card state,
    // which must not read as "never set up".
    id: "pa-7", provider: "deepseek", label: "Trial key",
    apiBase: null, orgSlug: null,
    createdAt: "2026-06-30T10:00:00Z", revokedAt: "2026-08-11T10:00:00Z",
    health: "unknown", lastCheckedAt: null, healthNote: null,
  },
];

// ⚠️ Each tier below is unhealthy in a DIFFERENT way, on purpose.
//   fast      — a healthy two-provider chain. The shape we want.
//   balanced  — every step is Anthropic. Looks like insurance, is not.
//   powerful  — one model only, and it has no price.
//   media     — points at Groq, whose key is failing, and at a model that
//               cannot do the job it is bound to.
const TIERS: Tier[] = [
  {
    slug: "fast", label: "Fast",
    blurb: "Quick answers. The cheapest tier, and the default everywhere.",
    jobs: [
      { tier: "fast", task: "chat", chain: [
        { model: "anthropic/claude-haiku-4", rank: 1 },
        { model: "gemini/gemini-2.5-flash", rank: 2 },
        { model: "openai/gpt-4o-mini", rank: 3 },
      ] },
      { tier: "fast", task: "embed", chain: [
        { model: "openai/text-embedding-3-large", rank: 1 },
      ] },
    ],
  },
  {
    slug: "balanced", label: "Balanced",
    blurb: "The everyday setting. Good answers without the top price.",
    jobs: [
      { tier: "balanced", task: "chat", chain: [
        { model: "anthropic/claude-sonnet-4", rank: 1 },
        { model: "anthropic/claude-haiku-4", rank: 2 },
      ] },
      { tier: "balanced", task: "image", chain: [
        { model: "openai/gpt-image-1", rank: 1 },
      ] },
    ],
  },
  {
    slug: "powerful", label: "Powerful",
    blurb: "For hard problems. Slower and much more expensive.",
    jobs: [
      { tier: "powerful", task: "chat", chain: [
        { model: "anthropic/claude-opus-4", rank: 1 },
      ] },
    ],
  },
  {
    slug: "media", label: "Media",
    blurb: "Speech and audio. Customers never pick this one directly.",
    jobs: [
      { tier: "media", task: "transcribe", chain: [
        { model: "groq/whisper-large-v3-turbo", rank: 1 },
        { model: "assemblyai/universal-2", rank: 2 },
      ] },
      { tier: "media", task: "speak", chain: [
        { model: "elevenlabs/eleven-turbo-v2", rank: 1 },
        { model: "openai/tts-1", rank: 2 },
      ] },
    ],
  },
];

const FAILOVERS: FailoverEvent[] = [
  {
    day: "2026-08-29", tier: "fast", task: "chat",
    model: "gemini/gemini-2.5-flash", rank: 2, requests: 412,
  },
  {
    day: "2026-08-28", tier: "media", task: "transcribe",
    model: "assemblyai/universal-2", rank: 2, requests: 27,
  },
  {
    day: "2026-08-27", tier: "fast", task: "chat",
    model: "groq/llama-3.3-70b", rank: 3, requests: 3,
  },
];

// ⚠️ Three shapes on purpose: a real price, a deliberate `absorbed` (D19.2)
// and an `unpriced` omission. Drawing the last two the same is how a draft
// price ships, so the design has to show both.
const RATES: ModelRate[] = [
  {
    model: "anthropic/claude-sonnet-4", task: "chat", unit: "tokens",
    mode: "priced", inputPer1k: "0.0300", outputPer1k: "0.1500",
    cachedInputPer1k: "0.0030", creditsPerUnit: "0",
  },
  {
    model: "anthropic/claude-haiku-4", task: "chat", unit: "tokens",
    mode: "priced", inputPer1k: "0.0080", outputPer1k: "0.0400",
    cachedInputPer1k: "0.0008", creditsPerUnit: "0",
  },
  {
    model: "openai/text-embedding-3-large", task: "embed", unit: "tokens",
    mode: "absorbed", inputPer1k: "0", outputPer1k: "0",
    cachedInputPer1k: "0", creditsPerUnit: "0",
  },
  {
    model: "groq/whisper-large-v3-turbo", task: "transcribe", unit: "minutes",
    mode: "unpriced", inputPer1k: "0", outputPer1k: "0",
    cachedInputPer1k: "0", creditsPerUnit: "0",
  },
  {
    model: "assemblyai/universal-2", task: "transcribe", unit: "minutes",
    mode: "priced", inputPer1k: "0", outputPer1k: "0",
    cachedInputPer1k: "0", creditsPerUnit: "0.4000",
  },
  {
    model: "openai/gpt-image-1", task: "image", unit: "images",
    mode: "priced", inputPer1k: "0", outputPer1k: "0",
    cachedInputPer1k: "0", creditsPerUnit: "12.0000",
  },
];

export const SAMPLE_CATALOG: AiCatalog = {
  tasks: TASKS,
  models: MODELS,
  rates: RATES,
  tiers: TIERS,
  accounts: ACCOUNTS,
  failovers: FAILOVERS,
};

/** What the backend still owes each screen, in an operator's words.
 *
 * ⚠️ These strings are the honest record of how much of this app is real. They
 * are shown to the reader, not kept in a comment, because a banner that says
 * "sample data" without saying what is missing teaches nobody anything. */
export const OWED = {
  models:
    "The catalog reads live. Context window, vendor price and the image and " +
    "reasoning chips come from `model_profile` (migration 012) — a model with " +
    "no profile row yet shows a dash, which is true rather than guessed.",
  tiers:
    "Ordered fallback needs a `rank` column on `tier_binding`, and the Router " +
    "needs to catch a vendor error and try the next step.",
  providers:
    "Accounts read live. Health needs a probe that nothing runs yet, so every " +
    "row would say `unknown`.",
  failovers:
    "`usage_event` has no column for the step that served, so no failover can " +
    "be proven.",
} as const;
