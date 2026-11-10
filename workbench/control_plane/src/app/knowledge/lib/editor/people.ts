// ── The person mention (WS-61 KB-2k1) ───────────────────────────────────────
//
// Pure. A person pick in the `@` menu stores a plain Markdown link,
// `[@Name](/people/<person_id>)`. The editor draws such a link as a person
// chip, as it draws a page link as a page chip (`pageChips.ts`). The link
// writes no `kb_links` row and sends no notice: the backlinks reader reads
// `/knowledge/pages/<uuid>` only, so a `/people/<id>` link is invisible to it.

/** The most people the `@` menu shows. */
export const MENTION_PEOPLE_MAX = 5;

/** One person the menu can offer: the fields of a directory row it needs. */
export interface PersonChoice {
  id: string;
  name: string;
  email: string | null;
}

const PERSON_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The address a person mention writes. */
export function personHref(personId: string): string {
  return `/people/${personId}`;
}

/** The text of a person mention: the name after an `@`. */
export function personLinkText(name: string): string {
  return `@${name.trim()}`;
}

/**
 * The person id of a link that draws as a person chip, or `null`. Only the
 * path `/people/<uuid>`, with an optional query or anchor, counts. A page
 * link, an external link and any other path of this app are not person chips.
 */
export function personChipId(href: string | null | undefined): string | null {
  const m = /^\/people\/([^/?#]+)\/?(?:[?#].*)?$/.exec(href ?? "");
  return m && PERSON_UUID.test(m[1]) ? m[1].toLowerCase() : null;
}

/**
 * The people the menu offers for the typed words, at most
 * `MENTION_PEOPLE_MAX`. `rows` is the directory answer, or `null` when the
 * read failed or was refused: then the menu has no People section. The
 * directory filters on the server, and this keeps a row only when each typed
 * word is in the name or the email, so a stale answer never shows.
 */
export function peopleChoices(
  rows: readonly { id: string; name: string; email?: string | null }[] | null | undefined,
  query: string,
): PersonChoice[] {
  if (!rows) return [];
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const out: PersonChoice[] = [];
  for (const r of rows) {
    if (!r?.id || !r.name) continue;
    const hay = `${r.name} ${r.email ?? ""}`.toLowerCase();
    if (!words.every((w) => hay.includes(w))) continue;
    out.push({ id: r.id, name: r.name, email: r.email ?? null });
    if (out.length === MENTION_PEOPLE_MAX) break;
  }
  return out;
}
