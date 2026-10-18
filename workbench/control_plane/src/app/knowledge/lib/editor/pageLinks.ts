// ── The page link picker (WS-61 KB-2b, §21.3) ──────────────────────────────
//
// Pure. `[[` in the editor and the link control both list the member's
// pages from here. A pick writes a Markdown link to `/knowledge/pages/<id>`
// (`pageHref`), and the gateway reads the links of the saved body later
// (KB-2h). The picker writes nothing to `kb_links`.

import { pageHref, pageIdOf } from "../links";
import type { PageSummary, Space } from "../types";

/** One page the picker can offer. */
export interface PageChoice {
  id: string;
  title: string;
  spaceName: string;
  updatedAt: string | null;
}

/** The most rows the picker shows. */
export const PAGE_CHOICES_MAX = 8;

/** The live pages of each space, as choices. A space that fails is left out. */
export async function loadPageChoices(
  spaces: readonly Pick<Space, "id" | "name" | "archived_at">[],
  listPages: (spaceId: string) => Promise<PageSummary[]>,
): Promise<PageChoice[]> {
  const open = spaces.filter((s) => !s.archived_at);
  const results = await Promise.allSettled(open.map((s) => listPages(s.id)));
  const out: PageChoice[] = [];
  results.forEach((r, i) => {
    if (r.status !== "fulfilled") return;
    for (const p of r.value) {
      if (p.state === "archived") continue;
      out.push({ id: p.id, title: p.title, spaceName: open[i].name, updatedAt: p.updated_at });
    }
  });
  return out;
}

/**
 * The pages whose title holds the typed words, best first: a title that
 * starts with them, then one that holds them. With no words, the pages
 * edited last come first. `exclude` leaves out the page being edited.
 */
export function matchPages(
  pages: readonly PageChoice[],
  query: string,
  opts: { exclude?: string | null; limit?: number } = {},
): PageChoice[] {
  const limit = opts.limit ?? PAGE_CHOICES_MAX;
  const q = query.trim().toLowerCase();
  const pool = pages.filter((p) => p.id !== opts.exclude);
  if (!q) {
    return [...pool]
      .sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "") || a.title.localeCompare(b.title))
      .slice(0, limit);
  }
  const scored = pool
    .map((p, index) => {
      const t = p.title.toLowerCase();
      const score = t.startsWith(q) ? 2 : t.includes(q) ? 1 : 0;
      return { p, index, score };
    })
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score || a.p.title.localeCompare(b.p.title) || a.index - b.index);
  return scored.slice(0, limit).map((x) => x.p);
}

/** The address a page link writes. */
export function pageLinkHref(pageId: string): string {
  return pageHref(pageId);
}

/** The page a link points at, or `null` for any other address. */
export function linkedPageId(href: string | null | undefined): string | null {
  if (!href) return null;
  return pageIdOf(href.split(/[?#]/)[0]);
}

/** True when `text` reads as an address a member typed, not as page words. */
export function looksLikeAddress(text: string): boolean {
  const t = text.trim();
  return /^(?:https?:\/\/|mailto:|\/|#)/i.test(t) || /^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(t);
}

/** The address a typed text links to: a bare domain gets `https://`. */
export function addressOf(text: string): string {
  const t = text.trim();
  if (/^(?:[a-z][a-z0-9+.-]*:|\/|#)/i.test(t)) return t;
  return `https://${t}`;
}

/** What the link control says when it refuses an address. */
export const LINK_REFUSED = "Use an address that starts with https://, or a page of this app.";

/**
 * True for an address a link may hold: `http:`, `https:`, `mailto:`,
 * `tel:`, a path of this app (`/…`, never `//…`) or an anchor (`#…`).
 */
export function isAllowedHref(href: string): boolean {
  const h = href.trim();
  if (!h || /[\s\u0000-\u001f]/.test(h)) return false;
  if (h.startsWith("#")) return true;
  if (h.startsWith("/")) return !h.startsWith("//") && !h.startsWith("/\\");
  return /^(?:https?:\/\/[^/\s]|mailto:|tel:)/i.test(h);
}
