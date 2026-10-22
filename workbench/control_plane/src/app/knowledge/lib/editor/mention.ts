// ── The `@` mention menu (WS-61, Notion's way to link) ──────────────────────
//
// Pure. `@` opens a menu with sections: Pages, People (KB-2k1), then Date. A
// page pick writes the same Markdown link as `[[` (`pageLinkHref`), so the
// backlinks of KB-2h read it unchanged. A person pick writes
// `[@Name](/people/<id>)` and nothing else. A date pick inserts the KB-2p
// date node. When the words match no page, the last row creates one (KB-2k3).

import { dateLabel, isIsoDate, todayIso } from "./date";
import { matchPages, type PageChoice } from "./pageLinks";
import { peopleChoices, type PersonChoice } from "./people";

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

export interface MentionPersonItem {
  kind: "person";
  id: string;
  section: "People";
  label: string;
  /** The email, as the second column. */
  hint: string;
  person: PersonChoice;
}

export interface MentionCreateItem {
  kind: "create";
  id: "create-page";
  section: "Create";
  label: string;
  /** The title of the page it makes: the typed words. */
  title: string;
}

export type MentionItem = MentionPageItem | MentionPersonItem | MentionDateItem | MentionCreateItem;

/** The longest page title the gateway takes. */
export const PAGE_TITLE_MAX = 300;

/**
 * The "Create page" row for a query, or `null`. It shows when the words are
 * not empty, match no page, and match no date, and the member can write in
 * the space of the open page. A page must be known: `pages` is `null` while
 * the list loads, and the row waits.
 */
export function createItem(
  pages: readonly PageChoice[] | null,
  query: string,
  opts: { exclude?: string | null; canCreate?: boolean; hasDate?: boolean } = {},
): MentionCreateItem | null {
  const title = query.trim();
  if (!opts.canCreate || !pages || !title || title.length > PAGE_TITLE_MAX || opts.hasDate) return null;
  if (matchPages(pages, title, { exclude: opts.exclude, limit: 1 }).length) return null;
  return { kind: "create", id: "create-page", section: "Create", label: `Create page "${title}"`, title };
}

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

/** The People rows: at most five. A failed read (`null`) gives none. */
export function personItems(
  rows: readonly { id: string; name: string; email?: string | null }[] | null | undefined,
  query: string,
): MentionPersonItem[] {
  return peopleChoices(rows, query).map((p) => ({
    kind: "person",
    id: `person:${p.id}`,
    section: "People",
    label: p.name,
    hint: p.email ?? "",
    person: p,
  }));
}

/** The rows of the `@` menu for a query: pages, people, dates, then Create page. */
export function buildMentionItems(
  pages: readonly PageChoice[] | null,
  query: string,
  opts: {
    exclude?: string | null;
    now?: Date;
    people?: readonly { id: string; name: string; email?: string | null }[] | null;
    canCreate?: boolean;
  } = {},
): MentionItem[] {
  const rows: MentionPageItem[] = matchPages(pages ?? [], query, { exclude: opts.exclude, limit: MENTION_PAGES_MAX }).map(
    (p) => ({ kind: "page", id: p.id, section: "Pages", label: p.title, hint: p.spaceName, glyph: p.icon ?? null, page: p }),
  );
  const dates = dateItems(query, opts.now);
  const create = createItem(pages, query, { exclude: opts.exclude, canCreate: opts.canCreate, hasDate: dates.length > 0 });
  return [...rows, ...personItems(opts.people, query), ...dates, ...(create ? [create] : [])];
}
