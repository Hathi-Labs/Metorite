/**
 * Reading an analytics response without trusting its declared type.
 *
 * ⚠️ **This file exists because the same defect has now landed three times in
 * one panel**, and each time it rendered as nothing rather than as an error:
 *
 * 1. `stuck.overdue` was an integer while the type declared a list. Nothing
 *    threw — `number.length` is `undefined`, and `undefined > 0` is false —
 *    so the Overdue section drew nothing. Found 2026-09-16.
 * 2. `stuck.stale` is a LIST of `{band, n}` while the type declared
 *    `Record<string, number>`. The panel asked `"under_7d" in data.stale`,
 *    which on an array tests INDICES, so it was always false and the ageing
 *    histogram drew an empty bar with no legend. Found 2026-09-17.
 * 3. `/analytics/stuck` answered 500 at every scope for a day, and the panel
 *    rendered nothing, because a rejected read is correctly treated as null.
 *
 * The common cause is not carelessness. `api.call` **casts** the response
 * rather than validating it, so a TypeScript interface here is a claim about
 * the server, not a check on it. The compiler then proves things about a
 * shape that may never arrive, and every downstream read is `undefined`
 * quietly flowing through comparisons that are all false.
 *
 * So: read defensively at the boundary, in one tested place, rather than
 * with an `Array.isArray` sprinkled at each use. A reader who wants to know
 * what the server really sends should find it here.
 */

/** One ageing band, however the server chose to shape it. */
export type StaleBand = { key: string; n: number };

/**
 * Normalise the ageing histogram from EITHER shape the server might send.
 *
 * ⚠️ Accepting both is deliberate, not indecision. The server sends a list
 * today and the type said a record; either could be true of a deployment
 * this client is talking to, and the panel must not go blank on the one it
 * did not expect. The cost is a few lines. The cost of guessing wrong has
 * been three silent blank sections.
 *
 * Order comes from the input when it is a list, because the bands are
 * ascending and that order is meaningful. The caller decides which bands it
 * knows how to draw.
 */
export function staleBands(input: unknown): StaleBand[] {
  if (Array.isArray(input)) {
    return input
      .map((entry) => {
        if (!entry || typeof entry !== "object") return null;
        const e = entry as Record<string, unknown>;
        // `band` is what the server calls it. `key` is accepted too, so a
        // rename on either side degrades to a missing band, not a crash.
        const key = typeof e.band === "string" ? e.band : e.key;
        const n = typeof e.n === "number" ? e.n : e.count;
        if (typeof key !== "string") return null;
        return { key, n: typeof n === "number" && Number.isFinite(n) ? n : 0 };
      })
      .filter((b): b is StaleBand => b !== null);
  }
  if (input && typeof input === "object") {
    return Object.entries(input as Record<string, unknown>).map(([key, n]) => ({
      key,
      n: typeof n === "number" && Number.isFinite(n) ? n : 0,
    }));
  }
  // Absent, null, or something else entirely. An empty histogram, which the
  // panel renders as "nothing is ageing" — the honest reading of no data.
  return [];
}

/** Look one band up by name. `0` when the server did not send it. */
export function bandCount(bands: StaleBand[], key: string): number {
  return bands.find((b) => b.key === key)?.n ?? 0;
}

/**
 * A list the server may have sent as something else.
 *
 * ⚠️ The `stuck.overdue` lesson generalised. Every list on an analytics
 * response goes through this, so a scalar where a list was declared becomes
 * an empty section rather than `undefined.slice` or a silent false.
 */
export function asList<T>(input: unknown): T[] {
  return Array.isArray(input) ? (input as T[]) : [];
}
