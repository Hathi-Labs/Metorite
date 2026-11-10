// ── The filters of the Search view (WS-61 KB-3d, `knowledge_base.md` §21.7) ──
//
// Pure, so that each rule has a fence with no DOM: how the chips live in the
// address, and what a Date preset sends. The view
// (`components/KnowledgeSearch.tsx`) only draws what these return.
//
// The address holds the choice and not the date: `?date=week` stays "past
// week" in a link that a member shares. The view turns it into the
// `updated_after` day when it asks.

/** The Date presets, in the order the chip's menu lists them. */
export type DatePreset = "week" | "month" | "year";

export const DATE_PRESETS: readonly { key: DatePreset | null; label: string; days: number | null }[] = [
  { key: null, label: "Any time", days: null },
  { key: "week", label: "Past week", days: 7 },
  { key: "month", label: "Past month", days: 30 },
  { key: "year", label: "Past year", days: 365 },
];

/** The gateway's `MAX_AUTHOR`: the longest email address. */
export const AUTHOR_MAX = 320;

export interface SearchFilters {
  /** A space id, or `null` for every space. */
  space: string | null;
  /** The email of the page's author, in lower case, or `null`. */
  author: string | null;
  date: DatePreset | null;
  /** Match the title only. */
  titleOnly: boolean;
}

export const NO_FILTERS: SearchFilters = { space: null, author: null, date: null, titleOnly: false };

/** An email as the Creator chip takes it, or `null` when the text is not one. */
export function cleanAuthor(raw: string | null | undefined): string | null {
  const email = (raw ?? "").trim().toLowerCase();
  if (!email || email.length > AUTHOR_MAX) return null;
  return /^[^\s@]+@[^\s@]+$/.test(email) ? email : null;
}

/** The filters in an address. A value the view does not know is dropped. */
export function filtersFromParams(params: { get(name: string): string | null }): SearchFilters {
  const date = params.get("date");
  const space = (params.get("space") ?? "").trim();
  return {
    space: space && space.length <= 64 ? space : null,
    author: cleanAuthor(params.get("author")),
    date: DATE_PRESETS.some((p) => p.key !== null && p.key === date) ? (date as DatePreset) : null,
    titleOnly: params.get("title") === "1",
  };
}

/** The pairs an address carries for these filters. An unset filter has none. */
export function filtersToPairs(f: SearchFilters): [string, string][] {
  const out: [string, string][] = [];
  if (f.space) out.push(["space", f.space]);
  if (f.author) out.push(["author", f.author]);
  if (f.date) out.push(["date", f.date]);
  if (f.titleOnly) out.push(["title", "1"]);
  return out;
}

/** One string for the filters, so an effect can depend on them. */
export function filtersKey(f: SearchFilters): string {
  return filtersToPairs(f)
    .map(([k, v]) => `${k}=${v}`)
    .join("&");
}

export function hasFilters(f: SearchFilters): boolean {
  return filtersToPairs(f).length > 0;
}

/**
 * The `updated_after` day of a preset: the day that many days before `now`,
 * as a UTC date (`YYYY-MM-DD`). `null` for "Any time".
 */
export function updatedAfter(date: DatePreset | null, now: Date = new Date()): string | null {
  const days = DATE_PRESETS.find((p) => p.key === date)?.days ?? null;
  if (days === null) return null;
  return new Date(now.getTime() - days * 86_400_000).toISOString().slice(0, 10);
}

/** The label of a preset, for the chip. */
export function dateLabel(date: DatePreset | null): string {
  return DATE_PRESETS.find((p) => p.key === date)?.label ?? "Any time";
}

/** The distinct authors of the hits, most frequent first, then by name. */
export function authorsOf(hits: readonly { author_email?: string | null }[]): string[] {
  const count = new Map<string, number>();
  for (const h of hits) {
    const email = cleanAuthor(h.author_email);
    if (email) count.set(email, (count.get(email) ?? 0) + 1);
  }
  return [...count.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([e]) => e);
}
