// The data contract the Operator Console's screens read — WRITTEN BEFORE THE
// BACKEND EXISTS, on purpose.
//
// 🔴 **This file is the whole point of building the UI first.** The screens are
// designed against these types, the sample data is typed against them, and the
// backend fills them in one endpoint at a time. A screen never reads a Console
// response shape directly, so a backend that arrives late — or arrives with a
// different shape — is a mapping change in `read.ts` and nowhere else.
//
// ⚠️ **A field here is a PROMISE, not a wish.** Every field must be one the
// Console can actually answer for, from a column that exists or one we are
// prepared to add. `contract.test.ts` carries a table of which fields are live
// today and which are still owed, and that table is the honest record of how
// much of this app is real.
//
// ⚠️ **Money and counts stay STRINGS where the database stores NUMERIC.** The
// ledger is NUMERIC(14,4). Parsing to float and re-formatting is how a total
// stops matching the sum of its rows. `number` here means the value is a true
// integer or a display-only ratio.

// ── Providers: the vendor accounts we hold ──────────────────────────────────
//
// ⚠️ **A provider can have MANY accounts.** That is the change this contract
// makes. Today `provider_credential` already allows it — one platform row plus
// one BYOK row per organization — but the surface drew a flat table and made it
// look like one key per vendor. Multiple platform accounts for one vendor is
// the case that matters: a second key is how we survive a rate limit or a
// suspended billing account without touching a tier.

/** Whether a vendor account answered the last time anything called it.
 *
 * ⚠️ `unknown` is the shipped value and must not render as healthy. Nothing
 * probes these yet, so a green dot would be a claim we cannot support. */
export type ProviderHealth = "ok" | "degraded" | "failing" | "unknown";

export type ProviderAccount = {
  id: string;
  /** The vendor slug — `anthropic`, `openai`, `groq`. Lowercase. */
  provider: string;
  label: string | null;
  /** NULL means the vendor's own default host. */
  apiBase: string | null;
  /** NULL means the PLATFORM account — the row the Router falls back to.
   *  A slug means BYOK: that one organization's own vendor account. */
  orgSlug: string | null;
  createdAt: string | null;
  revokedAt: string | null;
  health: ProviderHealth;
  /** NULL means nothing has ever probed it. Distinct from a probe that failed. */
  lastCheckedAt: string | null;
  /** Free-text reason the last probe gave. NULL when it passed or never ran. */
  healthNote: string | null;
};

// ── Models: the catalog, with enough metadata to SEARCH ─────────────────────

/** What a model is FOR. This is the filter vocabulary on the catalog page.
 *
 * ⚠️ **Not the same list as `task`.** A task is a job a tier binds (`chat`,
 * `transcribe`). A kind is a property of the model itself, and one model has
 * several — a vision-capable chat model is `chat` AND `vision`. Collapsing the
 * two is what made the old page unable to answer "show me models that can read
 * an image", which is the question D-AI-2 turns on. */
export type ModelKind =
  | "chat"
  | "reasoning"
  | "vision"
  | "image"
  | "transcribe"
  | "speak"
  | "embed";

export const MODEL_KINDS: ModelKind[] = [
  "chat", "reasoning", "vision", "image", "transcribe", "speak", "embed",
];

/** Plain words for the filter chips. No operator should need the schema open. */
export const KIND_LABEL: Record<ModelKind, string> = {
  chat: "Chat",
  reasoning: "Thinks first",
  vision: "Reads images",
  image: "Makes images",
  transcribe: "Speech to text",
  speak: "Text to speech",
  embed: "Search index",
};

export type CatalogModel = {
  /** The id the Router uses — `anthropic/claude-sonnet-4`. Unique. */
  id: string;
  label: string;
  provider: string;
  kinds: ModelKind[];
  /** Tokens. NULL when the vendor has not told us. */
  contextWindow: number | null;
  maxOutput: number | null;
  /** USD per million tokens. NULL means unpriced BY THE VENDOR in our record —
   *  it is not the same fact as `model_rate_card` being empty, which is what
   *  WE charge. Two different numbers, and confusing them inverts a margin. */
  inputPer1M: number | null;
  outputPer1M: number | null;
  /** The vendor's discounted CACHE-READ rate (013). NULL means untold, and a
   *  cache-hitting call cannot be costed until it is. */
  cachedInputPer1M: number | null;
  description: string;
  /** A `model_capability` row exists, so the Router will accept it. */
  declared: boolean;
  /** A `model_rate_card` row exists with a real mode, so it bills. */
  priced: boolean;
};

// ── Tiers: the customer-facing vocabulary, with ORDERED fallback ────────────
//
// 🔴 **The chain is the new idea and it needs a schema change.** `tier_binding`
// holds ONE model per (tier, task) with no ordering column, so there is nowhere
// to put a second choice. Everything below is designed here first and owed by
// the backend — `specs/ai_metering_and_analytics.md` §8 carries the slice.

export type ChainStep = {
  /** The model id. */
  model: string;
  /** 1 is the primary. Lower is tried first. Contiguous from 1. */
  rank: number;
};

export type TierJob = {
  /** The tier slug a customer picks — `fast`, `balanced`, `powerful`. */
  tier: string;
  /** The job — `chat`, `transcribe`. Matches `task.slug`. */
  task: string;
  /** Ordered. Empty means the job is unset, which is not the same as broken. */
  chain: ChainStep[];
};

export type Tier = {
  slug: string;
  label: string;
  /** What a customer is told this tier is for. Shown in their app, not here. */
  blurb: string;
  jobs: TierJob[];
};

// ── What WE charge — the rate card ──────────────────────────────────────────
//
// 🔴 **Not the same number as `CatalogModel.inputPer1M`.** That is what the
// VENDOR charges us. This is what a customer is billed. Reading one as the
// other inverts a margin, which is why the two live on different types with
// different units and are labelled differently everywhere they render.

export type ModelRate = {
  model: string;
  task: string;
  /** `tokens`, `minutes`, `images`… The unit decides WHICH column holds the
   *  price, so it is never decorative. */
  unit: string;
  /** `priced`, `absorbed` or `unpriced`. ⚠️ `absorbed` is a decision (D19.2)
   *  and `unpriced` is an omission. Drawing them the same ships a draft. */
  mode: string;
  /** Money, as the STRING the Console sent. The ledger is NUMERIC(14,4) and
   *  re-formatting a parsed float is how a total stops matching its rows. */
  inputPer1k: string;
  outputPer1k: string;
  cachedInputPer1k: string;
  creditsPerUnit: string;
};

// ── The routing record: what the Router actually did ────────────────────────
//
// ⚠️ **This is the evidence that a fallback worked.** A chain nobody can prove
// fired is a chain nobody trusts. `usage_event` needs a column for the step
// that served, and until it has one this stays sample-only.

export type FailoverEvent = {
  at: string;
  tier: string;
  task: string;
  /** The step that was tried and failed. */
  from: string;
  /** The step that answered. NULL means the whole chain failed. */
  to: string | null;
  reason: string;
  /** How many customer requests this affected. */
  requests: number;
};

// ── What the whole catalog read returns ─────────────────────────────────────

export type Task = { slug: string; label: string; natural_unit: string };

export type AiCatalog = {
  tasks: Task[];
  models: CatalogModel[];
  rates: ModelRate[];
  tiers: Tier[];
  accounts: ProviderAccount[];
  failovers: FailoverEvent[];
};

export const EMPTY_CATALOG: AiCatalog = {
  tasks: [], models: [], rates: [], tiers: [], accounts: [], failovers: [],
};
