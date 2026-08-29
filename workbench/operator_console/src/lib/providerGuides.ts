// How to get a key from each vendor — the part nobody remembers.
//
// 🔴 **Ported from the customer product, which is where it should never have
// been.** `control_plane/src/lib/model-types.ts` carried these until CP-5
// deleted the customer-side model picker (D32.7 — a customer never sees a
// model). The instructions themselves were correct and useful, and losing them
// would mean the next person installing a vendor key reads the vendor's own
// docs instead. This is a re-expression, not an import: `customer_console.md`
// §2.4 measures cross-imports from the customer workbench as ZERO and D35.4
// keeps that load-bearing.
//
// ⚠️ **A vendor with no entry is not an error.** `provider_credential` accepts
// any provider slug, and the form must keep working for one we have never
// written a guide for. The card simply shows no guide.
//
// ⚠️ URLs go stale. Each one was checked on 2026-08-29.

export type ProviderGuide = {
  /** One line: what this vendor is FOR, in our system. */
  description: string;
  /** Where a person goes to create the key. */
  setupUrl: string;
  steps: string[];
  /** What the key looks like, so a paste error is caught before the save. */
  keyLooksLike?: string;
};

export const PROVIDER_GUIDES: Record<string, ProviderGuide> = {
  anthropic: {
    description: "Claude models — Sonnet, Haiku and Opus, straight from the source.",
    setupUrl: "https://console.anthropic.com/settings/keys",
    steps: [
      "Sign in at console.anthropic.com.",
      "Open API Keys, then Create Key.",
      "Copy the key and paste it here.",
    ],
    keyLooksLike: "sk-ant-…",
  },
  openai: {
    description: "GPT models, image generation, Whisper and text to speech.",
    setupUrl: "https://platform.openai.com/api-keys",
    steps: [
      "Sign in at platform.openai.com.",
      "Open API Keys, then Create new secret key.",
      "Check that billing is set up, or every call fails with a quota error.",
    ],
    keyLooksLike: "sk-…",
  },
  google: {
    description: "Gemini models. The million-token window, and a free tier to start on.",
    setupUrl: "https://aistudio.google.com/apikey",
    steps: [
      "Open Google AI Studio and choose Get API key.",
      "Create a key. The free tier needs no card.",
    ],
    keyLooksLike: "AIza…",
  },
  openrouter: {
    description:
      "Two hundred models behind one key — useful as a backup provider, because " +
      "it fails independently of the vendors it resells.",
    setupUrl: "https://openrouter.ai/settings/keys",
    steps: [
      "Create an account at openrouter.ai.",
      "Open Settings, then Keys, then Create Key.",
      "Add credit if you want the paid models.",
    ],
    keyLooksLike: "sk-or-…",
  },
  groq: {
    description: "Open models served very fast, and cheap transcription.",
    setupUrl: "https://console.groq.com/keys",
    steps: [
      "Create a free account at console.groq.com.",
      "Open API Keys, then Create API Key.",
    ],
    keyLooksLike: "gsk_…",
  },
  deepseek: {
    description: "DeepSeek V3 and R1. The cheapest capable chat here by some way.",
    setupUrl: "https://platform.deepseek.com/api-keys",
    steps: [
      "Sign in at platform.deepseek.com.",
      "Open API Keys, then Create new API key.",
      "Add balance under Billing, or the key answers 402.",
    ],
    keyLooksLike: "sk-…",
  },
  mistral: {
    description: "Mistral models, including Codestral.",
    setupUrl: "https://console.mistral.ai/api-keys/",
    steps: ["Sign in at console.mistral.ai.", "Open API Keys, then Create new key."],
  },
  together: {
    description: "Open models at scale — Llama, Qwen, DeepSeek.",
    setupUrl: "https://api.together.ai/settings/api-keys",
    steps: ["Create an account at api.together.ai.", "Open Settings, then API Keys."],
  },
  assemblyai: {
    description:
      "Speech to text that names the speakers, and handles Hindi and English " +
      "in one recording. The cheapest per hour of the transcription options.",
    setupUrl: "https://www.assemblyai.com/dashboard/signup",
    steps: [
      "Create an account at assemblyai.com. It includes free credit.",
      "Copy the key from the dashboard home.",
    ],
  },
  deepgram: {
    description: "Speech to text with speaker names. A working alternative to AssemblyAI.",
    setupUrl: "https://console.deepgram.com/signup",
    steps: [
      "Create an account at console.deepgram.com. It includes free credit.",
      "Open API Keys, then Create a New API Key.",
    ],
  },
  elevenlabs: {
    description: "The most natural voices for reading text aloud. Billed per character.",
    setupUrl: "https://elevenlabs.io/app/settings/api-keys",
    steps: ["Sign in at elevenlabs.io.", "Open Settings, then API Keys."],
  },
};

/** Vendors we can offer a guide for, in a sensible order for a picker. */
export const KNOWN_PROVIDERS = Object.keys(PROVIDER_GUIDES).sort();

export function guideFor(provider: string): ProviderGuide | null {
  return PROVIDER_GUIDES[(provider || "").trim().toLowerCase()] ?? null;
}
