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
  FeedModel,
  ModelRate,
  ProviderAccount,
  Task,
  Tier,
  TierRate,
  VendorFeed,
} from "./contract";

const TASKS: Task[] = [
  { slug: "chat", label: "Answer questions", natural_unit: "1k tokens" },
  { slug: "image", label: "Make an image", natural_unit: "image" },
  { slug: "transcribe", label: "Write down speech", natural_unit: "minute" },
  { slug: "speak", label: "Read text aloud", natural_unit: "1k characters" },
  { slug: "embed", label: "Build a search index", natural_unit: "1k tokens" },
  { slug: "video", label: "Make a video", natural_unit: "seconds" },
  { slug: "music", label: "Make music", natural_unit: "seconds" },
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
    "The strongest and the most expensive. Worth it for hard analysis, wasteful for chat."),
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
    "Speech to text. Solid on clean audio, no speaker names."),
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
    "Transcription at several times real time. Billed per minute of audio."),
  M("deepseek/deepseek-chat", "DeepSeek V3", ["chat"],
    64000, 8192, 0.27, 1.1,
    "Very cheap per token. Good general chat, no images."),
  M("deepseek/deepseek-reasoner", "DeepSeek R1", ["chat", "reasoning"],
    64000, 8192, 0.55, 2.19,
    "Shows its working. Cheap for a reasoning model.",
    false),
  M("assemblyai/universal-2", "AssemblyAI Universal 2", ["transcribe"],
    null, null, null, null,
    "Names the speakers, handles Hindi and English in one recording."),
  M("elevenlabs/eleven-turbo-v2", "ElevenLabs Turbo v2", ["speak"],
    null, null, null, null,
    "The most natural voices here. Billed per character.",
    false),
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
    slug: "tier-fast", label: "Fast", registered: true, task: "chat",
    blurb: "Quick answers at the lowest price.",
    customerVisible: true,
    jobs: [
      { tier: "tier-fast", task: "chat", chain: [
        { model: "anthropic/claude-haiku-4", rank: 1 },
        { model: "gemini/gemini-2.5-flash", rank: 2 },
        { model: "openai/gpt-4o-mini", rank: 3 },
      ] },
    ],
  },
  {
    slug: "tier-balanced", label: "Balanced", registered: true, task: "chat",
    blurb: "The everyday setting - good answers, fair price.",
    customerVisible: true,
    jobs: [
      { tier: "tier-balanced", task: "chat", chain: [
        { model: "anthropic/claude-sonnet-4", rank: 1 },
        { model: "anthropic/claude-haiku-4", rank: 2 },
      ] },
      { tier: "tier-balanced", task: "image", chain: [
        { model: "openai/gpt-image-1", rank: 1 },
      ] },
    ],
  },
  {
    slug: "tier-powerful", label: "Powerful", registered: true, task: "chat",
    blurb: "The strongest models, for hard problems.",
    customerVisible: true,
    jobs: [
      { tier: "tier-powerful", task: "chat", chain: [
        { model: "anthropic/claude-opus-4", rank: 1 },
      ] },
    ],
  },
  {
    slug: "tier-code", label: "Code", registered: true, task: "chat",
    blurb: "Tuned for writing and fixing software.",
    customerVisible: true,
    jobs: [
      { tier: "tier-code", task: "chat", chain: [
        { model: "anthropic/claude-sonnet-4", rank: 1 },
        { model: "openai/gpt-4o", rank: 2 },
      ] },
    ],
  },
  {
    slug: "tier-vision", label: "Vision", registered: true, task: "vision",
    blurb: "Reads and understands images.",
    customerVisible: false,
    jobs: [],
  },
  {
    slug: "tier-image", label: "Image", registered: true, task: "image",
    blurb: "Makes images from a description.",
    customerVisible: true,
    jobs: [
      { tier: "tier-image", task: "image", chain: [
        { model: "openai/gpt-image-1", rank: 1 },
      ] },
    ],
  },
  {
    slug: "tier-stt", label: "Speech to text", registered: true, task: "transcribe",
    blurb: "Turns audio into text.",
    customerVisible: false,
    jobs: [
      { tier: "tier-stt", task: "transcribe", chain: [
        { model: "groq/whisper-large-v3-turbo", rank: 1 },
        { model: "assemblyai/universal-2", rank: 2 },
      ] },
    ],
  },
  {
    slug: "tier-tts", label: "Text to speech", registered: true, task: "speak",
    blurb: "Reads text aloud.",
    customerVisible: false,
    jobs: [
      { tier: "tier-tts", task: "speak", chain: [
        { model: "elevenlabs/eleven-turbo-v2", rank: 1 },
        { model: "openai/tts-1", rank: 2 },
      ] },
    ],
  },
  {
    slug: "tier-embed", label: "Search index", registered: true, task: "embed",
    blurb: "Builds the vectors behind search.",
    customerVisible: false,
    jobs: [
      { tier: "tier-embed", task: "embed", chain: [
        { model: "openai/text-embedding-3-large", rank: 1 },
      ] },
    ],
  },
  // The two capabilities NOTHING can serve yet: the tier and the price can
  // exist first, and the day the Router grows the verb nothing else moves.
  {
    slug: "tier-video", label: "Video", registered: true, task: "video",
    blurb: "Makes video from a description.",
    customerVisible: false,
    jobs: [],
  },
  {
    slug: "tier-music", label: "Music", registered: true, task: "music",
    blurb: "Makes music and sound.",
    customerVisible: false,
    jobs: [],
  },
  // A GHOST: a hand-typed binding whose tier is not in the registry. It
  // serves, the board flags it, and it cannot be priced until registered.
  {
    slug: "legacy-chat", label: "legacy-chat", registered: false, task: null,
    blurb: "",
    customerVisible: false,
    jobs: [
      { tier: "legacy-chat", task: "chat", chain: [
        { model: "openai/gpt-4o-mini", rank: 1 },
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

// ⚠️ The feed sample models the three states the page must draw:
//   * a DRIFT — deepseek-chat's profile says $0.27/1M in, upstream now says
//     $0.28 (the real move, 2026-08). The card must warn;
//   * an AVAILABLE model with everything known — one click declares it;
//   * an available model in a mode we cannot serve — shown, not declarable.
const FM = (
  id: string, mode: string, task: string | null, invocation: string | null,
  ctx: number | null, out: number | null,
  inP: string | null, outP: string | null, cached: string | null,
  over: Partial<FeedModel> = {},
): FeedModel => ({
  id, provider: id.split("/")[0], mode, task, invocation,
  contextWindow: ctx, maxOutput: out,
  inputPer1M: inP, outputPer1M: outP, cachedInputPer1M: cached,
  readsImages: false, thinksFirst: false, deprecatedOn: null,
  ...over,
});

const FEED: VendorFeed = {
  syncedAt: "2026-08-30T06:00:00Z",
  source: "github",
  models: 2716,
  rows: [
    // Upstream's claim about a DECLARED model, prices moved under us.
    FM("deepseek/deepseek-chat", "chat", "chat", "acompletion",
      131072, 8192, "0.280000", "0.420000", "0.070000"),
    // And one that agrees exactly — no chip, nothing to do.
    FM("anthropic/claude-sonnet-4", "chat", "chat", "acompletion",
      200000, 64000, "3.000000", "15.000000", "0.300000",
      { readsImages: true, thinksFirst: true }),
  ],
  available: [
    FM("deepseek/deepseek-reasoner", "chat", "chat", "acompletion",
      65536, 8192, "0.550000", "2.190000", "0.140000",
      { thinksFirst: true }),
    FM("groq/whisper-large-v3", "audio_transcription", "transcribe",
      "atranscription", null, null, null, null, null),
    // A mode the Router has no verb for: visible, never declarable.
    FM("groq/rerank-english-v3", "rerank", null, null,
      null, null, "0.100000", null, null),
    FM("gemini/gemini-2.5-flash-lite", "chat", "chat", "acompletion",
      1048576, 65536, "0.100000", "0.400000", "0.025000",
      { readsImages: true }),
  ],
};

const TIER_RATES: TierRate[] = [
  {
    tier: "tier-fast", task: "chat", unit: "tokens", mode: "priced",
    inputPer1k: "0.0080", outputPer1k: "0.0400",
    cachedInputPer1k: "0.0008", creditsPerUnit: "0",
  },
  {
    tier: "tier-balanced", task: "chat", unit: "tokens", mode: "priced",
    inputPer1k: "0.0300", outputPer1k: "0.1500",
    cachedInputPer1k: "0.0030", creditsPerUnit: "0",
  },
  {
    tier: "tier-embed", task: "embed", unit: "tokens", mode: "absorbed",
    inputPer1k: "0", outputPer1k: "0",
    cachedInputPer1k: "0", creditsPerUnit: "0",
  },
  {
    tier: "tier-stt", task: "transcribe", unit: "minutes", mode: "priced",
    inputPer1k: "0", outputPer1k: "0",
    cachedInputPer1k: "0", creditsPerUnit: "0.4000",
  },
  {
    tier: "tier-image", task: "image", unit: "images", mode: "priced",
    inputPer1k: "0", outputPer1k: "0",
    cachedInputPer1k: "0", creditsPerUnit: "12.0000",
  },
];

export const SAMPLE_CATALOG: AiCatalog = {
  tasks: TASKS,
  models: MODELS,
  rates: RATES,
  tiers: TIERS,
  accounts: ACCOUNTS,
  accountsKnown: true,
  failovers: FAILOVERS,
  feed: FEED,
  tierRates: TIER_RATES,
  creditPrice: {
    inrPerCredit: "1", usdToInr: "88",
    effectiveFrom: "2026-08-20T00:00:00Z",
  },
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
    "no profile row yet shows a dash, which is true rather than guessed. " +
    "Vendor facts fetch live from litellm's price map (migration 014); " +
    "'Fetch the latest' fills `vendor_price_feed` and this page reads it back.",
  tiers:
    "Chains read and save live, ranks included (migration 011), and the " +
    "Router walks them (D-AI-6). The registry and the tier prices are " +
    "migration 015: what a customer pays is keyed on the tier they picked " +
    "(D67), and a failover changes our cost, never their price.",
  providers:
    "Accounts read live. Health needs a probe that nothing runs yet, which " +
    "is why the cards no longer draw a health chip at all.",
  failovers:
    "Live since migration 013: `served_rank` above 1 on a usage row is the " +
    "proof. This sample shows the SHAPE of the table; the real one starts " +
    "empty and that is good news.",
} as const;
