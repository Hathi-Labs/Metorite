// ── The `@` mention menu (WS-61, Notion's way to link) ──────────────────────
//
// Pure. `@` opens a menu with two sections: Pages, then Date. A page pick
// writes the same Markdown link as `[[` (`pageLinkHref`), so the backlinks of
// KB-2h read it unchanged. A date pick inserts the KB-2p date node.
// People are not here: a mention of a person needs KB-2k1.

import { dateLabel, isIsoDate, todayIso } from "./date";
import { matchPages, type PageChoice } from "./pageLinks";

/** The most pages the menu shows. */
export const MENTION_PAGES_MAX = 8;

/**
 * `@` opens only at the start of a line or after a space, so `a@b.com`
 * opens nothing. These are the `allowedPrefixes` of the Suggestion plugin.
 */
export const MENTION_PREFIXES: string[] = [" "];

export interface MentionPageItem {
  kind: "page";
  id: string;
  section: "Pages";
  label: string;
  hint: string;
  /** The page emoji, or `null` for the file icon. */
  glyph: string | null;
  page: PageChoice;
}

export interface MentionDateItem {
  kind: "date";
  id: string;
  section: "Date";
  label: string;
  /** The day it inserts, `YYYY-MM-DD`. */
  iso: string;
  hint: string;
}

export type MentionItem = MentionPageItem | MentionDateItem;

/** `iso` moved by `days`, in the member's own calendar. */
export function shiftDay(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  return todayIso(new Date(y, m - 1, d + days));
}

const RELATIVE = [
  { key: "today", label: "Today", days: 0 },
  { key: "tomorrow", label: "Tomorrow", days: 1 },
  { key: "yesterday", label: "Yesterday", days: -1 },
] as const;

/** The day a typed query names (`2026-10-19`), or `null`. A bad day is `null`. */
export function parseTypedDate(query: string): string | null {
  const q = query.trim();
  return isIsoDate(q) ? q : null;
}

/** The date rows for a query: a relative day that starts with it, or the typed day. */
export function dateItems(query: string, now: Date = new Date()): MentionDateItem[] {
  const q = query.trim().toLowerCase();
  const today = todayIso(now);
  const out: MentionDateItem[] = [];
  for (const r of RELATIVE) {
    if (q && !r.key.startsWith(q)) continue;
    const iso = shiftDay(today, r.days);
    out.push({ kind: "date", id: `date:${r.key}`, section: "Date", label: r.label, iso, hint: dateLabel(iso) });
  }
  const typed = parseTypedDate(q);
  if (typed && !out.some((i) => i.iso === typed)) {
    out.push({ kind: "date", id: `date:${typed}`, section: "Date", label: dateLabel(typed), iso: typed, hint: typed });
  }
  return out;
}

/** The rows of the `@` menu for a query: pages first, then dates. */
export function buildMentionItems(
  pages: readonly PageChoice[],
  query: string,
  opts: { exclude?: string | null; now?: Date } = {},
): MentionItem[] {
  const rows: MentionPageItem[] = matchPages(pages, query, { exclude: opts.exclude, limit: MENTION_PAGES_MAX }).map(
    (p) => ({ kind: "page", id: p.id, section: "Pages", label: p.title, hint: p.spaceName, glyph: p.icon ?? null, page: p }),
  );
  return [...rows, ...dateItems(query, opts.now)];
}
