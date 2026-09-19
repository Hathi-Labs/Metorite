// Finding a model in a catalog of hundreds — search, facets and sort.
//
// 🔴 **The old surface had a free-text box and nothing else.** With one
// provider that was survivable. OpenRouter alone exposes two hundred models,
// and an operator asked "which of these can read an image" had no way to ask
// it. Every judgement below is a pure function so `modelSearch.test.ts` can
// hold it — this app's suite carries no React renderer, so anything expressed
// in JSX is untested by construction.

import { KIND_LABEL, type CatalogModel, type ModelKind } from "./contract";
import type { Tone } from "./tone";

export type ModelStatus = "costed" | "undeclared" | "nokey" | "costblind";

/** The SUPPLY-side state of a model — what we know about calling it.
 *
 * 🔴 **"Ready to sell" left this vocabulary with D67.** Selling is priced on
 * the TIER now, so a model cannot be un-sellable by itself — what it can be
 * is un-COSTABLE: declared but with no vendor price recorded, which makes
 * every margin that touches it read as unknown. The tier board owns the
 * selling states; this page owns the supply ones.
 *
 * 🔴 **`nokey` exists because the seed proved it must (owner report,
 * 2026-08-30).** The product ships tier-stt bound to a groq model, so
 * `groq/whisper-large-v3-turbo` is DECLARED on every fresh install — and
 * with no groq key it read as merely "costs blind", underselling "every
 * call to this fails". A declared model whose vendor holds no live
 * platform key now says so, in red.
 *
 * ⚠️ **Order matters.** Undeclared outranks nokey (nothing can be called
 * before it exists), nokey outranks costblind (a model we cannot call at
 * all has a worse problem than an unknown price). */
export function statusOf(m: CatalogModel, armed: string[]): ModelStatus {
  if (!m.declared) return "undeclared";
  if (!armed.includes(m.provider)) return "nokey";
  if (m.inputPer1M === null) return "costblind";
  return "costed";
}

export const STATUS_LABEL: Record<ModelStatus, string> = {
  costed: "costed",
  undeclared: "not connected",
  nokey: "no key installed",
  costblind: "costs blind",
};

export type Filters = {
  query: string;
  /** Empty means no kind filter. */
  kinds: ModelKind[];
  /** Empty means every provider. */
  providers: string[];
  /** Empty means every status. */
  statuses: ModelStatus[];
};

export const NO_FILTERS: Filters = {
  query: "", kinds: [], providers: [], statuses: [],
};

/** Free-text match across everything a person might type.
 *
 * ⚠️ **The DESCRIPTION is searched too.** An operator looking for a cheap
 * transcription model types "cheap", not a model id. Restricting the match to
 * the id makes the box useless for the only query it is really used for.
 *
 * 🔴 **The KIND LABELS are in the haystack, added 2026-09-19.** Capability was
 * reachable only through a row of chips, so "which models read images" needed
 * a chip and could not be typed. Measured the same day: that row carried seven
 * chips and only two ever had models behind them — five read `0` on every
 * install. Putting the labels here is what let the row go.
 *
 * ⚠️ **The LABEL, not the slug.** An operator types "reads images", not
 * "vision". The slug is matched too, because it costs nothing and an engineer
 * reading `model_capability` types that instead. */
export function matchesQuery(m: CatalogModel, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  // Every whitespace-separated word must appear SOMEWHERE. "claude fast"
  // should find a fast Claude, and an OR would return every Claude.
  const kinds = m.kinds.map((k) => `${k} ${KIND_LABEL[k]}`).join(" ");
  const hay =
    `${m.id} ${m.label} ${m.provider} ${m.description} ${kinds}`.toLowerCase();
  return q.split(/\s+/).every((word) => hay.includes(word));
}

/** Does this model do ALL of the selected kinds?
 *
 * 🔴 **AND, not OR, and the labels must say so.** The question an operator
 * actually has is "a chat model that can also read an image" — one model doing
 * both. An OR returns every chat model plus every vision model and answers a
 * question nobody asked. D-AI-2 turns on exactly this query, because the image
 * tier follows the chat model when that model declares `vision`. */
export function matchesKinds(m: CatalogModel, kinds: ModelKind[]): boolean {
  if (kinds.length === 0) return true;
  return kinds.every((k) => m.kinds.includes(k));
}

export function filterModels(
  models: CatalogModel[],
  f: Filters,
  /** Providers with a live platform key - `statusOf`'s context. */
  armed: string[] = [],
): CatalogModel[] {
  const providers = new Set(f.providers.map((p) => p.toLowerCase()));
  const statuses = new Set(f.statuses);
  return models.filter(
    (m) =>
      matchesQuery(m, f.query) &&
      matchesKinds(m, f.kinds) &&
      (providers.size === 0 || providers.has(m.provider.toLowerCase())) &&
      (statuses.size === 0 || statuses.has(statusOf(m, armed))),
  );
}

export type SortKey = "name" | "context" | "cheapest" | "provider";

/** ⚠️ **A NULL number sorts LAST in every direction.** "We do not know the
 * context window" is not "the context window is zero", and putting unknowns at
 * the top of a cheapest-first list would recommend them. */
function nullsLast(a: number | null, b: number | null, dir: 1 | -1): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return (a - b) * dir;
}

export function sortModels(models: CatalogModel[], by: SortKey): CatalogModel[] {
  const out = [...models];
  if (by === "name") out.sort((a, b) => a.label.localeCompare(b.label));
  else if (by === "provider")
    out.sort(
      (a, b) =>
        a.provider.localeCompare(b.provider) || a.label.localeCompare(b.label),
    );
  else if (by === "context")
    out.sort((a, b) => nullsLast(a.contextWindow, b.contextWindow, -1));
  else out.sort((a, b) => nullsLast(a.inputPer1M, b.inputPer1M, 1));
  return out;
}

export type Facet<T> = { value: T; count: number };

/** How many models each chip would show, counted against the OTHER filters.
 *
 * 🔴 **A facet counted against the whole catalog lies.** With "anthropic"
 * selected, a "Speech to text" chip reading 14 promises fourteen results and
 * delivers none. Each facet is therefore counted with its OWN dimension
 * dropped from the filter and every other dimension kept — the standard
 * faceted-search rule, and the only one where the number on the chip is the
 * number of rows you get by clicking it. */
export function kindFacets(
  models: CatalogModel[], f: Filters, kinds: ModelKind[],
  armed: string[] = [],
): Facet<ModelKind>[] {
  return kinds.map((k) => ({
    value: k,
    count: filterModels(models, { ...f, kinds: [...f.kinds, k] }, armed).length,
  }));
}

/** How close a model is to the date its vendor switches it off.
 *
 * 🔴 **A declared model's card never said this, and the risk is large.**
 * Measured against the live feed on 2026-09-19: 798 models carry a retirement
 * date, 337 of them ALREADY PAST and 214 inside ninety days. Declare one of
 * those and the tier pointing at it fails the day the vendor pulls it, with
 * nothing on this page having said so. The "available" table showed the badge;
 * the card you look at afterwards did not.
 *
 * ⚠️ **Silent past ninety days, deliberately.** A 2027 date is a fact nobody
 * can act on today, and a card that warns about everything trains an operator
 * to read none of it. Past and imminent are what change a decision.
 *
 * ⚠️ **"retired" in the past tense, and DANGER.** A date that has gone by is
 * not a warning about the future — the model may already be refusing calls.
 */
export type Retirement = { label: string; tone: Tone; days: number };

export function retirementOf(
  deprecatedOn: string | null,
  today: Date,
): Retirement | null {
  if (!deprecatedOn) return null;
  const when = Date.parse(`${deprecatedOn}T00:00:00Z`);
  if (Number.isNaN(when)) return null;
  const midnight = Date.UTC(
    today.getUTCFullYear(),
    today.getUTCMonth(),
    today.getUTCDate(),
  );
  const days = Math.round((when - midnight) / 86_400_000);
  if (days < 0) {
    return { label: `retired ${deprecatedOn}`, tone: "danger", days };
  }
  if (days <= 90) {
    return {
      label: days === 0 ? "retires today" : `retires in ${days} days`,
      tone: "warn",
      days,
    };
  }
  return null;
}

/** Which tiers point at this model, and where in each chain it sits.
 *
 * 🔴 **The card could not answer "does this one matter?"** A declared model
 * that some tier serves from is load-bearing — changing or removing it moves
 * customer traffic. A declared model no tier points at is doing nothing at
 * all. Both drew identically, so the page gave an operator no way to tell the
 * two apart before acting.
 *
 * ⚠️ **Rank is carried, because first and second choice are different jobs.**
 * A tier's primary serves every call. Its backup serves only during an outage,
 * so swapping one is a far smaller act than swapping the other.
 *
 * ⚠️ **Sorted by rank then tier**, so a model's own card always lists the
 * places it is a primary before the places it is a fallback.
 */
export type TierUse = { tier: string; task: string; rank: number };

export function tiersUsing(modelId: string, tiers: TierLike[]): TierUse[] {
  const out: TierUse[] = [];
  for (const t of tiers) {
    for (const j of t.jobs) {
      for (const step of j.chain) {
        if (step.model === modelId) {
          out.push({ tier: t.label || t.slug, task: j.task, rank: step.rank });
        }
      }
    }
  }
  return out.sort((a, b) => a.rank - b.rank || a.tier.localeCompare(b.tier));
}

/** The shape `tiersUsing` needs — deliberately narrower than `Tier`, so the
 *  function is testable without building a whole catalog. */
export type TierLike = {
  slug: string;
  label: string;
  jobs: { task: string; chain: { model: string; rank: number }[] }[];
};

/** Where a model sits in one tier's chain, in an operator's words. */
export function rankWord(rank: number): string {
  if (rank <= 1) return "1st choice";
  if (rank === 2) return "backup";
  return `backup ${rank - 1}`;
}

/** The states that mean somebody has work to do on this model.
 *
 * 🔴 **Four chips became one question, 2026-09-19.** The state row let you
 * filter to `costed`, `costs blind`, `no key installed` and `not connected`
 * separately. Three of those are the same question — "what is not ready" —
 * and the fourth is "show me the ones with no problem", which is what the
 * unfiltered page already shows. Four controls for one question is why the
 * row read as a puzzle.
 *
 * ⚠️ **`costed` is deliberately absent.** It is the healthy state, every card
 * carries its own badge, and a filter for "show me what is fine" answers
 * nothing an operator acts on.
 */
export const ATTENTION_STATUSES: ModelStatus[] = [
  "costblind",
  "nokey",
  "undeclared",
];

/** How many models need somebody's attention.
 *
 * ⚠️ Counted through `filterModels`, never a second rule, so the number on the
 * toggle and the list it produces cannot disagree. */
export function attentionCount(
  models: CatalogModel[],
  f: Filters,
  armed: string[],
): number {
  return filterModels(models, { ...f, statuses: ATTENTION_STATUSES }, armed)
    .length;
}

/** The capability chips worth drawing: the ones with models behind them.
 *
 * 🔴 **A chip that always reads `0` is a control that has never once been
 * useful.** Measured 2026-09-19: of seven kind chips, `chat` had 42 models,
 * `transcribe` had 1, and the other five had none on any install.
 *
 * ⚠️ **One kind left means NO row.** Filtering a list to the only kind it
 * contains returns the same list, so the control does nothing but take space
 * and invite a click. Capability is still typeable — `matchesQuery` reads the
 * kind labels.
 */
export function usefulKindFacets(
  facets: Facet<ModelKind>[],
  selected: ModelKind[],
): Facet<ModelKind>[] {
  const live = facets.filter(
    (k) => k.count > 0 || selected.includes(k.value),
  );
  return live.length > 1 ? live : [];
}


/** How many model cards to draw before asking. */
export const MODEL_PAGE = 24;

/** The page of cards to draw, and how many sit behind it.
 *
 * 🔴 **A catalog is unbounded and this page was not.** Forty-three declared
 * models drew a 13483px page on 2026-09-19. One OpenRouter key declares two
 * hundred, which is five times that — and the browser builds every card, every
 * feed lookup and every drift comparison before anybody sees the first one.
 *
 * ⚠️ **`expanded` shows everything, and it is the operator's choice.** A hard
 * cap with no way past it makes a model invisible, and "search for it" is no
 * answer when you do not know its name.
 */
export function pageOf<T>(
  rows: T[],
  expanded: boolean,
  size: number = MODEL_PAGE,
): { shown: T[]; hidden: number } {
  if (expanded || rows.length <= size) return { shown: rows, hidden: 0 };
  return { shown: rows.slice(0, size), hidden: rows.length - size };
}

/** The line above the list. Says what is shown and, when nothing is, why. */
export function resultLine(shown: number, total: number, f: Filters): string {
  if (total === 0) return "No models in the catalog yet.";
  if (shown === 0) {
    const bits: string[] = [];
    if (f.query.trim()) bits.push(`"${f.query.trim()}"`);
    if (f.kinds.length > 0) bits.push(`all of ${f.kinds.join(" + ")}`);
    if (f.providers.length > 0) bits.push(f.providers.join(" or "));
    return bits.length > 0
      ? `No model matches ${bits.join(", ")}. Remove a filter.`
      : "No models match.";
  }
  if (shown === total) return `${total} model${total === 1 ? "" : "s"}.`;
  return `${shown} of ${total} models.`;
}

/** Toggle one value in a filter list — the operation every chip performs. */
export function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

// ── Display helpers ─────────────────────────────────────────────────────────

/** A token count a person can read at a glance: 200000 becomes "200K".
 *
 * ⚠️ **NULL is an em dash, never 0.** A context window we were never told is
 * not a context window of zero, and "0 tokens" reads as a broken model rather
 * than a missing column. */
export function formatTokens(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return "—";
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${Number.isInteger(m) ? m : m.toFixed(1)}M`;
  }
  if (n >= 1000) {
    const k = n / 1000;
    return `${Number.isInteger(k) ? k : k.toFixed(1)}K`;
  }
  return String(n);
}

/** The vendor's own price, per million tokens.
 *
 * 🔴 **This is what the VENDOR charges us, not what we charge a customer.**
 * Those are two different numbers on two different tables, and reading one as
 * the other inverts a margin. The label rendered beside it must say "we pay". */
export function formatVendorPrice(
  inPer1M: number | null,
  outPer1M: number | null,
): string {
  if (inPer1M === null && outPer1M === null) return "—";
  const one = (n: number | null) => (n === null ? "?" : `$${n}`);
  return `${one(inPer1M)} in / ${one(outPer1M)} out`;
}
