/**
 * Single source of truth for LLM provider metadata.
 *
 * To add a new provider, add ONE entry to the PROVIDERS array below.
 * Everything else — UI colours, icons, setup guides, env-var maps, and the
 * fallback key check in the model picker — is derived automatically.
 *
 * Mirror list: apps/services/gateway/gateway/routes/settings.py (_PROVIDER_ENV_MAP)
 * Backend allowlist: apps/services/gateway/gateway/routes/integrations.py (_ALLOWED_ENV_KEYS)
 * Both are kept in sync via a comment; see the "Adding a provider" instructions
 * in project-docs/system_architecture.md.
 */

export interface ProviderGuide {
  description: string;
  setup_url: string;
  docs_url: string;
  instructions: string[];
}

export interface ProviderMeta {
  id: string;
  label: string;
  /** Env-var name that gates this provider. Empty string for local providers. */
  envVar: string;
  colour: string;
  icon: string;
  /** Step-by-step setup guide shown in Settings → Models. Omit for local providers. */
  guide?: ProviderGuide;
}

// ─── Canonical provider registry ────────────────────────────────────────────
// Add a new object here to register a provider everywhere in the UI at once.
// You MUST also mirror the change in the backend — see the comment at top.

export const PROVIDERS: readonly ProviderMeta[] = [
  {
    id: "gemini",
    label: "Google Gemini",
    envVar: "GEMINI_API_KEY",
    colour: "bg-blue-500/15 text-blue-400 border-blue-800/40",
    icon: "G",
    guide: {
      description: "Google's Gemini model family — powers all three tiers by default.",
      setup_url: "https://aistudio.google.com/apikey",
      docs_url: "https://ai.google.dev/gemini-api/docs/models",
      instructions: [
        "Go to Google AI Studio → Get API key.",
        "Create a new key (free tier available, no credit card required).",
        "Copy the key — it starts with 'AIza…'.",
      ],
    },
  },
  {
    id: "openai",
    label: "OpenAI",
    envVar: "OPENAI_API_KEY",
    colour: "bg-green-500/15 text-green-400 border-green-800/40",
    icon: "⬡",
    guide: {
      description: "OpenAI GPT-4o, o3-mini, and other models.",
      setup_url: "https://platform.openai.com/api-keys",
      docs_url: "https://platform.openai.com/docs/models",
      instructions: [
        "Log in to platform.openai.com.",
        "Navigate to API Keys → Create new secret key.",
        "Copy the key — it starts with 'sk-…'.",
        "Ensure billing is set up in Settings → Billing.",
      ],
    },
  },
  {
    id: "anthropic",
    label: "Anthropic",
    envVar: "ANTHROPIC_API_KEY",
    colour: "bg-orange-500/15 text-orange-300 border-orange-800/40",
    icon: "◆",
    guide: {
      description: "Direct access to Claude models (Sonnet, Haiku, Opus).",
      setup_url: "https://console.anthropic.com/settings/keys",
      docs_url: "https://docs.anthropic.com/en/api/getting-started",
      instructions: [
        "Log in to console.anthropic.com.",
        "Navigate to Settings → API Keys → Create Key.",
        "Copy the key — it starts with 'sk-ant-…'.",
      ],
    },
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    envVar: "OPENROUTER_API_KEY",
    colour: "bg-rose-500/15 text-rose-300 border-rose-800/40",
    icon: "⊕",
    guide: {
      description: "200+ models via one key — Claude, GPT, Gemini, Llama, DeepSeek and more.",
      setup_url: "https://openrouter.ai/settings/keys",
      docs_url: "https://openrouter.ai/docs",
      instructions: [
        "Create a free account at openrouter.ai.",
        "Go to Settings → Keys → Create Key.",
        "Copy the key — it starts with 'sk-or-…'.",
        "Add credits if you want to use paid models (free tier covers some).",
      ],
    },
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    envVar: "DEEPSEEK_API_KEY",
    colour: "bg-cyan-500/15 text-cyan-300 border-cyan-800/40",
    icon: "🐋",
    guide: {
      description: "DeepSeek direct API — DeepSeek-V3 (chat) and DeepSeek-R1 (reasoner) at very competitive pricing.",
      setup_url: "https://platform.deepseek.com/api-keys",
      docs_url: "https://platform.deepseek.com/docs",
      instructions: [
        "Log in to platform.deepseek.com.",
        "Go to API Keys → Create new API key.",
        "Copy the key — it starts with 'sk-…'.",
        "Add balance in the Billing section to enable API access.",
      ],
    },
  },
  {
    id: "github",
    label: "GitHub Copilot",
    envVar: "GITHUB_TOKEN",
    colour: "bg-sky-500/15 text-sky-300 border-sky-800/40",
    icon: "✦",
    guide: {
      description: "GitHub Copilot models — GPT-4o, Claude Sonnet, o3-mini at no extra cost.",
      setup_url: "https://github.com/settings/tokens",
      docs_url: "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens",
      instructions: [
        "Go to GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens.",
        "Create a new token with no specific repository access.",
        "Under 'Permissions', enable 'Copilot' (read).",
        "Copy the token — it starts with 'github_pat_…'.",
        "Requires an active GitHub Copilot subscription.",
      ],
    },
  },
  {
    id: "groq",
    label: "Groq",
    envVar: "GROQ_API_KEY",
    colour: "bg-cat-12/15 text-cat-12 border-cat-12/30",
    icon: "⚡",
    guide: {
      description: "Ultra-fast inference for Llama, Mixtral, and Gemma models.",
      setup_url: "https://console.groq.com/keys",
      docs_url: "https://console.groq.com/docs/openai",
      instructions: [
        "Create a free account at console.groq.com.",
        "Navigate to API Keys → Create API Key.",
        "Copy the key — it starts with 'gsk_…'.",
      ],
    },
  },
  {
    id: "mistral",
    label: "Mistral AI",
    envVar: "MISTRAL_API_KEY",
    colour: "bg-indigo-500/15 text-indigo-300 border-indigo-800/40",
    icon: "🌪",
    guide: {
      description: "Mistral AI models — Mistral Small, Medium, Large, and Codestral.",
      setup_url: "https://console.mistral.ai/api-keys/",
      docs_url: "https://docs.mistral.ai/api/",
      instructions: [
        "Log in to console.mistral.ai.",
        "Go to API Keys → Create new key.",
        "Copy the key.",
      ],
    },
  },
  {
    id: "together",
    label: "Together AI",
    envVar: "TOGETHER_API_KEY",
    colour: "bg-teal-500/15 text-teal-300 border-teal-800/40",
    icon: "🤝",
    guide: {
      description: "Together AI — open models at scale (Llama, Qwen, DeepSeek).",
      setup_url: "https://api.together.ai/settings/api-keys",
      docs_url: "https://docs.together.ai/docs/introduction",
      instructions: [
        "Create an account at api.together.ai.",
        "Go to Settings → API Keys.",
        "Copy your key.",
      ],
    },
  },
  {
    id: "ollama",
    label: "Ollama (local)",
    envVar: "",
    colour: "bg-violet-500/15 text-violet-400 border-violet-800/40",
    icon: "🦙",
  },
  {
    id: "vllm",
    label: "vLLM (local)",
    envVar: "VLLM_BASE_URL",
    colour: "bg-violet-500/15 text-violet-400 border-violet-800/40",
    icon: "⚡",
  },
] as const;

// ─── Derived lookup maps ─────────────────────────────────────────────────────
// These are computed once at module load and used throughout the app.

/** id → env-var name (excludes local providers with no env var) */
export const PROVIDER_ENV_MAP: Record<string, string> = Object.fromEntries(
  PROVIDERS.filter((p) => p.envVar).map((p) => [p.id, p.envVar])
);

/** id → Tailwind colour classes (includes "unknown" fallback) */
export const PROVIDER_COLOURS: Record<string, string> = {
  ...Object.fromEntries(PROVIDERS.map((p) => [p.id, p.colour])),
  unknown: "bg-muted text-muted-foreground border-border",
};

/** id → display icon */
export const PROVIDER_ICONS: Record<string, string> = Object.fromEntries(
  PROVIDERS.filter((p) => p.icon).map((p) => [p.id, p.icon])
);

/** id → setup guide (only providers that have one) */
export const PROVIDER_GUIDES: Record<string, ProviderGuide> = Object.fromEntries(
  PROVIDERS.filter((p) => p.guide).map((p) => [p.id, p.guide!])
);

/** id → display label */
export const PROVIDER_LABELS: Record<string, string> = Object.fromEntries(
  PROVIDERS.map((p) => [p.id, p.label])
);
