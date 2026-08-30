// Finding a model in a catalog of hundreds — search, facets and sort.
//
// 🔴 **The old surface had a free-text box and nothing else.** With one
// provider that was survivable. OpenRouter alone exposes two hundred models,
// and an operator asked "which of these can read an image" had no way to ask
// it. Every judgement below is a pure function so `modelSearch.test.ts` can
// hold it — this app's suite carries no React renderer, so anything expressed
// in JSX is untested by construction.

import type { CatalogModel, ModelKind } from "./contract";

export type ModelStatus = "costed" | "undeclared" | "costblind";

/** The SUPPLY-side state of a model — what we know about calling it.
 *
 * 🔴 **"Ready to sell" left this vocabulary with D67.** Selling is priced on
 * the TIER now, so a model cannot be un-sellable by itself — what it can be
 * is un-COSTABLE: declared but with no vendor price recorded, which makes
 * every margin that touches it read as unknown. The tier board owns the
 * selling states; this page owns the supply ones.
 *
 * ⚠️ **Order matters and it is the same order as `readiness.ts`.** Undeclared
 * outranks costblind because nothing can be costed before it can be served. */
export function statusOf(m: CatalogModel): ModelStatus {
  if (!m.declared) return "undeclared";
  if (m.inputPer1M === null) return "costblind";
  return "costed";
}

export const STATUS_LABEL: Record<ModelStatus, string> = {
  costed: "costed",
  undeclared: "not connected",
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
 * the id makes the box useless for the only query it is really used for. */
export function matchesQuery(m: CatalogModel, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  // Every whitespace-separated word must appear SOMEWHERE. "claude fast"
  // should find a fast Claude, and an OR would return every Claude.
  const hay = `${m.id} ${m.label} ${m.provider} ${m.description}`.toLowerCase();
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

export function filterModels(models: CatalogModel[], f: Filters): CatalogModel[] {
  const providers = new Set(f.providers.map((p) => p.toLowerCase()));
  const statuses = new Set(f.statuses);
  return models.filter(
    (m) =>
      matchesQuery(m, f.query) &&
      matchesKinds(m, f.kinds) &&
      (providers.size === 0 || providers.has(m.provider.toLowerCase())) &&
      (statuses.size === 0 || statuses.has(statusOf(m))),
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
): Facet<ModelKind>[] {
  return kinds.map((k) => ({
    value: k,
    count: filterModels(models, { ...f, kinds: [...f.kinds, k] }).length,
  }));
}

export function providerFacets(models: CatalogModel[], f: Filters): Facet<string>[] {
  const all = [...new Set(models.map((m) => m.provider))].sort();
  return all.map((p) => ({
    value: p,
    count: filterModels(models, { ...f, providers: [p] }).length,
  }));
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
