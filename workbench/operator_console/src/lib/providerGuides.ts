// The vendors we can install a key for — the one registry, and the whole list.
//
// 🔴 **The slug is a litellm PROVIDER ID, and that is the only rule that
// matters here.** `main.py` resolves a chain step's vendor as
// `step.model.split("/", 1)[0]` and looks `provider_credential` up on exactly
// that string. So a card whose slug is not the prefix litellm uses can never
// arm anything: the key installs, the model declares, the tier binds, and the
// first request answers `503 no provider credential configured for 'gemini'`.
//
// 🔴 **Two entries were wrong that way and are fixed here.** `google` was the
// slug for Gemini and `together` for Together AI. litellm calls them `gemini`
// and `together_ai`, so both cards were dead ends that cost an operator an
// install, a model declaration, a tier binding and a 503 before telling them
// anything. `tests/unit/test_operator_console_vendor_slugs.py` now checks every
// slug in this file against `litellm.provider_list`, so the next one fails in
// CI instead of on the owner's first customer.
//
// ⚠️ **A vendor with no entry is still installable.** `check_provider` accepts
// any slug of the right shape, and the "Something else" card exists for the
// vendor we have not written up. This list is the easy path, not a gate.
//
// ⚠️ **What is deliberately NOT here.** `azure` needs an API version and a
// deployment host as well as a key, and the install form has one secret field
// and one API base — an azure card would look installable and half-work.
// `bedrock` and `vertex_ai` want a credential file rather than a key.
// `ollama` and `vllm` need no key at all, and `check_secret` refuses a
// credential shorter than 16 characters, so their cards could never be
// completed. A card that cannot be finished is worse than no card.
//
// ⚠️ URLs go stale. The eleven original entries were checked on 2026-08-29.
// The six added on 2026-08-30 — xai, cohere, perplexity, cerebras,
// fireworks_ai, github — were written from the vendor's documented console
// path and re-checked by request on the same day.

/** What we would USE a vendor for. Matches `task_catalog.slug`, plus `vision`,
 *  which is a property of a chat model rather than a job a tier binds. */
export type VendorJob = "chat" | "vision" | "transcribe" | "speak" | "image" | "embed";

export type ProviderGuide = {
  /** The vendor's own name for itself. The slug is the litellm id, which is
   *  not always presentable — `together_ai`, `fireworks_ai`. */
  label: string;
  /** One line: what this vendor is FOR, in our system. */
  description: string;
  /** Where a person goes to create the key. */
  setupUrl: string;
  /** Where the models and their names are listed. */
  docsUrl?: string;
  steps: string[];
  /** What the key looks like, so a paste error is caught before the save. */
  keyLooksLike?: string;
  /** ⚠️ What this vendor could serve, NOT what the Router can call today.
   *  `ROUTED_TODAY` is the second fact and the page must show both. */
  serves: VendorJob[];
};

export const PROVIDER_GUIDES: Record<string, ProviderGuide> = {
  anthropic: {
    label: "Anthropic",
    description: "Claude models — Sonnet, Haiku and Opus, straight from the source.",
    setupUrl: "https://console.anthropic.com/settings/keys",
    docsUrl: "https://docs.anthropic.com/en/docs/about-claude/models",
    steps: [
      "Sign in at console.anthropic.com.",
      "Open API Keys, then Create Key.",
      "Copy the key and paste it here.",
    ],
    keyLooksLike: "sk-ant-…",
    serves: ["chat", "vision"],
  },
  openai: {
    label: "OpenAI",
    description: "GPT models, image generation, Whisper and text to speech.",
    setupUrl: "https://platform.openai.com/api-keys",
    docsUrl: "https://platform.openai.com/docs/models",
    steps: [
      "Sign in at platform.openai.com.",
      "Open API Keys, then Create new secret key.",
      "Check that billing is set up, or every call fails with a quota error.",
    ],
    keyLooksLike: "sk-…",
    serves: ["chat", "vision", "transcribe", "speak", "image", "embed"],
  },
  gemini: {
    label: "Google Gemini",
    description: "Gemini models. The million-token window, and a free tier to start on.",
    setupUrl: "https://aistudio.google.com/apikey",
    docsUrl: "https://ai.google.dev/gemini-api/docs/models",
    steps: [
      "Open Google AI Studio and choose Get API key.",
      "Create a key. The free tier needs no card.",
    ],
    keyLooksLike: "AIza…",
    serves: ["chat", "vision", "image", "embed"],
  },
  openrouter: {
    label: "OpenRouter",
    description:
      "Two hundred models behind one key — useful as a backup provider, because " +
      "it fails independently of the vendors it resells.",
    setupUrl: "https://openrouter.ai/settings/keys",
    docsUrl: "https://openrouter.ai/models",
    steps: [
      "Create an account at openrouter.ai.",
      "Open Settings, then Keys, then Create Key.",
      "Add credit if you want the paid models.",
    ],
    keyLooksLike: "sk-or-…",
    serves: ["chat", "vision"],
  },
  deepseek: {
    label: "DeepSeek",
    description: "DeepSeek V3 and R1. The cheapest capable chat here by some way.",
    setupUrl: "https://platform.deepseek.com/api-keys",
    docsUrl: "https://api-docs.deepseek.com/quick_start/pricing",
    steps: [
      "Sign in at platform.deepseek.com.",
      "Open API Keys, then Create new API key.",
      "Add balance under Billing, or the key answers 402.",
    ],
    keyLooksLike: "sk-…",
    serves: ["chat"],
  },
  groq: {
    label: "Groq",
    description: "Open models served very fast, and cheap Whisper transcription.",
    setupUrl: "https://console.groq.com/keys",
    docsUrl: "https://console.groq.com/docs/models",
    steps: [
      "Create a free account at console.groq.com.",
      "Open API Keys, then Create API Key.",
    ],
    keyLooksLike: "gsk_…",
    serves: ["chat", "transcribe"],
  },
  mistral: {
    label: "Mistral AI",
    description: "Mistral models, including Codestral. European hosting.",
    setupUrl: "https://console.mistral.ai/api-keys/",
    docsUrl: "https://docs.mistral.ai/getting-started/models/models_overview/",
    steps: ["Sign in at console.mistral.ai.", "Open API Keys, then Create new key."],
    serves: ["chat", "vision", "embed"],
  },
  xai: {
    label: "xAI Grok",
    description: "Grok models. Strong on current events, because it reads the feed.",
    setupUrl: "https://console.x.ai/",
    docsUrl: "https://docs.x.ai/docs/models",
    steps: [
      "Sign in at console.x.ai.",
      "Open API Keys, then Create API Key.",
      "Add credit under Billing before the first call.",
    ],
    keyLooksLike: "xai-…",
    serves: ["chat", "vision"],
  },
  together_ai: {
    label: "Together AI",
    description: "Open models at scale — Llama, Qwen, DeepSeek, at low cost.",
    setupUrl: "https://api.together.ai/settings/api-keys",
    docsUrl: "https://docs.together.ai/docs/serverless-models",
    steps: ["Create an account at api.together.ai.", "Open Settings, then API Keys."],
    serves: ["chat", "vision", "image", "embed"],
  },
  cohere: {
    label: "Cohere",
    description: "Command models, and the embeddings most worth using for search.",
    setupUrl: "https://dashboard.cohere.com/api-keys",
    docsUrl: "https://docs.cohere.com/docs/models",
    steps: [
      "Create an account at cohere.com.",
      "Open the dashboard, then API Keys.",
      "The trial key is rate limited. Create a production key to serve customers.",
    ],
    serves: ["chat", "embed"],
  },
  perplexity: {
    label: "Perplexity",
    description: "Chat that searches the web first and cites what it read.",
    setupUrl: "https://www.perplexity.ai/account/api/keys",
    docsUrl: "https://docs.perplexity.ai/getting-started/models",
    steps: [
      "Sign in at perplexity.ai.",
      "Open Settings, then API, then Generate.",
      "Buy credit first. The key does not work on a Pro subscription alone.",
    ],
    keyLooksLike: "pplx-…",
    serves: ["chat"],
  },
  cerebras: {
    label: "Cerebras",
    description: "The fastest Llama and Qwen inference available. Free tier to try.",
    setupUrl: "https://cloud.cerebras.ai/",
    docsUrl: "https://inference-docs.cerebras.ai/models/overview",
    steps: [
      "Create an account at cloud.cerebras.ai.",
      "Open API Keys, then Create Secret Key.",
    ],
    keyLooksLike: "csk-…",
    serves: ["chat"],
  },
  fireworks_ai: {
    label: "Fireworks AI",
    description: "Open models with fast cold starts. A second source for Llama and Qwen.",
    setupUrl: "https://app.fireworks.ai/settings/users/api-keys",
    docsUrl: "https://fireworks.ai/models",
    steps: [
      "Create an account at fireworks.ai.",
      "Open Settings, then API Keys, then Create API Key.",
    ],
    keyLooksLike: "fw_…",
    serves: ["chat", "vision", "image", "embed"],
  },
  github: {
    label: "GitHub Models",
    description:
      "GPT, Llama and Phi through a GitHub token. Rate limited and free, which " +
      "makes it a cheap backup rather than a primary.",
    setupUrl: "https://github.com/settings/personal-access-tokens",
    docsUrl: "https://docs.github.com/en/github-models/prototyping-with-ai-models",
    steps: [
      "Open GitHub Settings, then Developer settings, then Fine-grained tokens.",
      "Generate a token with no repository access.",
      "Under Account permissions, set Models to Read-only.",
      "Copy the token before you leave the page. GitHub shows it once.",
    ],
    keyLooksLike: "github_pat_…",
    serves: ["chat"],
  },
  assemblyai: {
    label: "AssemblyAI",
    description:
      "Speech to text that names the speakers, and handles Hindi and English " +
      "in one recording. The cheapest per hour of the transcription options.",
    setupUrl: "https://www.assemblyai.com/dashboard/signup",
    docsUrl: "https://www.assemblyai.com/docs/speech-to-text/pre-recorded-audio",
    steps: [
      "Create an account at assemblyai.com. It includes free credit.",
      "Copy the key from the dashboard home.",
    ],
    serves: ["transcribe"],
  },
  deepgram: {
    label: "Deepgram",
    description: "Speech to text with speaker names. A working alternative to AssemblyAI.",
    setupUrl: "https://console.deepgram.com/signup",
    docsUrl: "https://developers.deepgram.com/docs/models-languages-overview",
    steps: [
      "Create an account at console.deepgram.com. It includes free credit.",
      "Open API Keys, then Create a New API Key.",
    ],
    serves: ["transcribe"],
  },
  elevenlabs: {
    label: "ElevenLabs",
    description: "The most natural voices for reading text aloud. Billed per character.",
    setupUrl: "https://elevenlabs.io/app/settings/api-keys",
    docsUrl: "https://elevenlabs.io/docs/capabilities/text-to-speech",
    steps: ["Sign in at elevenlabs.io.", "Open Settings, then API Keys."],
    serves: ["speak"],
  },
};

/** Vendors we can offer a guide for. Insertion order, which is roughly the
 *  order somebody should consider them in — not alphabetical, because
 *  alphabetical puts AssemblyAI above Anthropic and says nothing. */
export const KNOWN_PROVIDERS = Object.keys(PROVIDER_GUIDES);

export function guideFor(provider: string): ProviderGuide | null {
  return PROVIDER_GUIDES[(provider || "").trim().toLowerCase()] ?? null;
}

/** The vendor's own name, or the slug back when we have never heard of it. */
export function vendorLabel(provider: string): string {
  return guideFor(provider)?.label ?? provider;
}

// ── What the Router can actually call ───────────────────────────────────────

/** 🔴 **The Router serves ONE of the six tasks.** `main.py` declares exactly
 *  one `/v1/` route — `/v1/chat/completions` — so a transcription or a voice
 *  key installs correctly, encrypts correctly, and is then called by nothing.
 *  H-46 owns the remaining endpoints and D61.1 decided their shape.
 *
 *  ⚠️ This must be shown, not hidden. An operator who installs an ElevenLabs
 *  key and hears silence will look for the fault in the key. */
export const ROUTED_TODAY: VendorJob[] = ["chat", "vision"];

/** Can anything this vendor does reach a customer today? */
export function isRoutedToday(provider: string): boolean {
  const g = guideFor(provider);
  // ⚠️ An unknown vendor is assumed routable. We do not know what it serves,
  // and a warning we cannot justify trains people to ignore warnings.
  if (!g) return true;
  return g.serves.some((job) => ROUTED_TODAY.includes(job));
}

// ── Sections: what a vendor is FOR ──────────────────────────────────────────
//
// ⚠️ **Derived from `serves`, never stored a second time.** A `section` field
// beside `serves` is two facts that can disagree, and the one that renders
// would quietly win.

export type SectionKey = "chat" | "listen" | "voice" | "other";

export type Section = {
  key: SectionKey;
  title: string;
  /** One honest line about whether installing here does anything yet. */
  note: string;
};

export const SECTIONS: Section[] = [
  {
    key: "chat",
    title: "Chat and reasoning",
    note:
      "These arm the Router today. Two vendors here, bound as a tier's first " +
      "and second choice, is what turns a vendor outage into a retry.",
  },
  {
    key: "listen",
    title: "Speech to text",
    note:
      "The Router has no transcription endpoint yet (H-46), so a key installed " +
      "here is stored and not yet called. Install it when the endpoint lands.",
  },
  {
    key: "voice",
    title: "Text to speech",
    note:
      "The Router has no speech endpoint yet (H-46). Same as above — the key " +
      "is safe here, and nothing calls it.",
  },
  {
    key: "other",
    title: "Vendors we have not written up",
    note:
      "Installed by hand, or by somebody before this page existed. The slug " +
      "must match the prefix on the model ids you declare.",
  },
];

/** Which section a vendor belongs in. Chat wins when a vendor does both —
 *  OpenAI transcribes, and nobody looks for OpenAI under Speech to text. */
export function sectionOf(provider: string): SectionKey {
  const g = guideFor(provider);
  if (!g) return "other";
  if (g.serves.includes("chat")) return "chat";
  if (g.serves.includes("transcribe")) return "listen";
  if (g.serves.includes("speak")) return "voice";
  return "other";
}
