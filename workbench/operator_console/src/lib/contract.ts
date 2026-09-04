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
  /** USD per unit the vendor charges US for a job a token price cannot cost
   *  (019, H-78): a `transcribe` MINUTE, a `speak` CHARACTER, an `image`.
   *  NULL means untold, and the board draws a dash rather than a guess.
   *
   * ⚠️ **`number | null`, unlike `FeedModel`'s STRINGS, and the difference is
   *  deliberate.** These read the PROFILE, which the board only ever DISPLAYS
   *  and computes a suggestion from — no value here is ever POSTed back. The
   *  feed's strings are, so they stay strings. Same `num()` rule every other
   *  price on this shape follows. */
  perMinuteUsd: number | null;
  perCharacterUsd: number | null;
  perImageUsd: number | null;
  /** The OFF-PEAK token rates (migration 023). NULL means this vendor charges
   *  one rate all day, which is every model but DeepSeek's two.
   *
   * ⚠️ **`inputPer1M` above is the PEAK rate.** It keeps its plain name
   *  because R6 forbids a rename in place, and the vendor feed already fills
   *  it with the peak number.
   *
   * ⚠️ These change what a call COST us and never what a customer pays. D67
   *  keys the charge on the tier. A tier PRICE derives from the peak rate
   *  always — owner directive, 2026-09-04 — so no suggestion on this board
   *  may read an off-peak number. */
  inputOffpeakPer1M: number | null;
  outputOffpeakPer1M: number | null;
  cachedInputOffpeakPer1M: number | null;
  /** When the off-peak window opens and closes, `HH:MM` in UTC. Both or
   *  neither. The range MAY wrap midnight, and DeepSeek's does (16:30 to
   *  00:30), so anything that reads these must handle start > end. */
  offpeakStartUtc: string | null;
  offpeakEndUtc: string | null;
  /** Prompt tokens above which the long-context rates apply, and those rates.
   *  NULL means one rate at every size. Without them a large document
   *  under-bills by half, on exactly the calls that cost most. */
  contextTierThreshold: number | null;
  inputLongPer1M: number | null;
  outputLongPer1M: number | null;
  cachedInputLongPer1M: number | null;
  description: string;
  /** A `model_capability` row exists, so the Router will accept it. */
  declared: boolean;
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
  /** In `tier_catalog` (015). A tier can serve while unregistered — a ghost
   *  from a hand-typed binding — and the board flags it rather than hiding
   *  it, but only registered tiers can carry a price. */
  registered: boolean;
  /** The ONE kind of job this tier serves (D68) — `chat` for the quality
   *  bands, the capability for the rest. NULL/absent = uncategorised (a
   *  ghost, or a pre-016 row): the board groups it separately and the
   *  Console's mismatch refusals do not fire. */
  task?: string | null;
  /** In `tier_catalog.customer_visible` (021, D-AI-3). TRUE means a customer
   *  picks this tier on purpose. FALSE means the Router or the app selects
   *  it — `GET /my/tiers` serves no such row, so it reaches no picker. A
   *  ghost reads FALSE, because a picker cannot offer what the registry does
   *  not hold. A Console that predates 021 reads TRUE, which is the column's
   *  own default. */
  customerVisible: boolean;
};

// ── What WE charge — the rate card ──────────────────────────────────────────
//
// 🔴 **Not the same number as `CatalogModel.inputPer1M`.** That is what the
// VENDOR charges us. This is what a customer is billed. Reading one as the
// other inverts a margin, which is why the two live on different types with
// different units and are labelled differently everywhere they render.

/** 🔴 What a CUSTOMER pays for one (tier, job) — D67, migration 015.
 *
 * The tier is the product and the model is supply: a failover moves our
 * cost, never their price, and two tiers sharing one model can still charge
 * differently. This is the card billing reads. `ModelRate` below is the
 * retired model-keyed card, kept as readable history. */
export type TierRate = {
  tier: string;
  task: string;
  unit: string;
  /** `priced`, `absorbed` or `unpriced` — same G-4 vocabulary. */
  mode: string;
  /** Money as the STRINGS the Console sent.
   *
   * ⚠️ **The per-1k fields are the OLD scale** (migration 024, release one of
   *  two). Both scales cross the wire while the Console and this app deploy
   *  apart, and a later release removes them. Read `…Per1m` in new code. */
  inputPer1k: string;
  outputPer1k: string;
  cachedInputPer1k: string;
  /** 🔴 The scale of record from 2026-09-04 (owner directive). Every vendor
   *  quotes per million, so the card now speaks the same unit as the cost it
   *  is derived from — and a reader comparing the two carries no factor of
   *  1000 in their head. */
  inputPer1m: string;
  outputPer1m: string;
  cachedInputPer1m: string;
  creditsPerUnit: string;
};

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
// 🔴 **LIVE since migration 013.** `usage_event.served_rank` records the
// position of the step that answered, and rank above 1 is a customer request
// the primary did not serve. The shape below is exactly what the database can
// PROVE — the earlier draft carried `from` and `reason`, which the row does
// not hold (the primary at that moment is a join against re-bindable history,
// and the reason lives in the `router.failover` log line). A field the data
// cannot back is a story, not a record.

export type FailoverEvent = {
  /** The day, ISO. Aggregated per day so a bad afternoon is one row. */
  day: string;
  tier: string;
  task: string;
  /** The step that ANSWERED. */
  model: string;
  /** Its position in the chain. Always 2 or more here. */
  rank: number;
  /** How many customer requests that step carried that day. */
  requests: number;
};

// ── The vendor feed: upstream facts, fetched instead of typed ───────────────
//
// 🔴 **LIVE since migration 014 (owner directive, 2026-08-30).** No vendor
// publishes a machine-readable price list, so the reliable source is litellm's
// community price map — keyed on the SAME provider ids the Router routes on,
// and bundled offline inside the litellm package the Console already ships.
// The feed is a cache of upstream claims: billing still reads `model_profile`,
// which only an explicit operator save changes. The feed's job is to make that
// save a one-click copy, and to make a vendor's price change VISIBLE (drift)
// instead of silent.

export type FeedModel = {
  /** Vendor-qualified, the Router's grammar: `deepseek/deepseek-chat`. */
  id: string;
  provider: string;
  /** litellm's word for what it does — `chat`, `embedding`, `rerank`… */
  mode: string;
  /** OUR task slug, mapped server-side in ONE place (`feed.MODE_MAP`).
   *  NULL means a mode we cannot serve yet — the row informs, but there is
   *  no one-click declare for it. */
  task: string | null;
  invocation: string | null;
  contextWindow: number | null;
  maxOutput: number | null;
  /** ⚠️ STRINGS, unlike CatalogModel's display prices — the feed is
   *  NUMERIC(14,6) and these values get POSTED back into profiles, so a
   *  float round-trip here would write its own noise into the database.
   *  `Number()` on them only to compare and to draw. */
  inputPer1M: string | null;
  outputPer1M: string | null;
  cachedInputPer1M: string | null;
  /** The per-unit costs (019, H-78), in the PROFILE's unit already.
   *
   * 🔴 **`perMinuteUsd`, and the feed table stores per SECOND.** The Console's
   *  feed-read projection multiplies by 60 once, server-side, so what arrives
   *  here is per minute and the copy onto a profile is a straight copy. No
   *  code in this app multiplies by 60 — `feed.test.ts` asserts on the source
   *  text that none ever does. */
  perMinuteUsd: string | null;
  perCharacterUsd: string | null;
  perImageUsd: string | null;
  readsImages: boolean;
  thinksFirst: boolean;
  /** The vendor's own retirement date, when litellm records one. */
  deprecatedOn: string | null;
};

export type VendorFeed = {
  /** NULL means never synced — an empty feed is a state, not an error. */
  syncedAt: string | null;
  /** `github` (live) or `packaged:litellm` (offline snapshot). */
  source: string | null;
  /** How many models the whole feed table holds. */
  models: number;
  /** Feed facts for models ALREADY declared or profiled — the drift surface. */
  rows: FeedModel[];
  /** What CONNECTED vendors offer that nobody declared. Vendors we hold no
   *  live platform key for are excluded: that is a brochure, not an offer. */
  available: FeedModel[];
};

export const EMPTY_FEED: VendorFeed = {
  syncedAt: null, source: null, models: 0, rows: [], available: [],
};

/** The credit's own price (migration 017) — what one credit SELLS for.
 *
 * ⚠️ STRINGS, exact — the same money rule as every price on this wire.
 * Billing never reads it: a call bills credits, and the tier card owns how
 * many (D67). This converts rupees to credits when somebody BUYS them.
 * Null until the owner saves one (H-42). */
export type CreditPrice = {
  inrPerCredit: string;
  /** INR per USD — the saved PLANNING rate margins convert with. */
  usdToInr: string;
  effectiveFrom: string | null;
};

// ── What the whole catalog read returns ─────────────────────────────────────

export type Task = { slug: string; label: string; natural_unit: string };

export type AiCatalog = {
  tasks: Task[];
  models: CatalogModel[];
  rates: ModelRate[];
  tiers: Tier[];
  accounts: ProviderAccount[];
  /** ⚠️ False when the credential read FAILED while the catalog read worked.
   *  `accounts` is then `[]` by absence of evidence, not by fact — and an
   *  empty list must not be presented as "no credential is installed". The
   *  page banner and the go-live rail read this and say UNKNOWN instead. */
  accountsKnown: boolean;
  failovers: FailoverEvent[];
  feed: VendorFeed;
  /** What customers pay, per (tier, job) — D67. The card billing reads. */
  tierRates: TierRate[];
  /** The credit's own rupee price — null until the owner sets it (H-42). */
  creditPrice: CreditPrice | null;
};

export const EMPTY_CATALOG: AiCatalog = {
  tasks: [], models: [], rates: [], tiers: [], accounts: [],
  accountsKnown: true, failovers: [], feed: EMPTY_FEED, tierRates: [],
  creditPrice: null,
};
