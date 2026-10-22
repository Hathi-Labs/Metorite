// ── The addresses of Knowledge (WS-61 KB-2a and KB-3b, `knowledge_base.md` §17) ─
//
// `/knowledge` is the Spaces view. `?space=<id>` selects a space.
// `/knowledge/pages/<id>` is one page, and a search hit opens there.
// `/knowledge/search?q=<words>` is the Search view, so a search is a link.
// `/knowledge?space=<id>&view=archived` is the archived pages of a space (KB-2d).

import { NO_FILTERS, filtersToPairs, type SearchFilters } from "./searchFilters";

export const KNOWLEDGE_HOME = "/knowledge";

/** The path of the Search view, with no query. */
export const KNOWLEDGE_SEARCH = `${KNOWLEDGE_HOME}/search`;

export function spaceHref(spaceId: string | null): string {
  return spaceId ? `${KNOWLEDGE_HOME}?space=${encodeURIComponent(spaceId)}` : KNOWLEDGE_HOME;
}

export function pageHref(pageId: string, opts: { edit?: boolean; q?: string } = {}): string {
  const base = `${KNOWLEDGE_HOME}/pages/${encodeURIComponent(pageId)}`;
  if (opts.edit) return `${base}?edit=1`;
  // KB-3d: a hit opens its page with the words, and the match bar marks them.
  return opts.q?.trim() ? `${base}?q=${encodeURIComponent(opts.q.trim())}` : base;
}

/** The `view` of a space that lists its archived pages (KB-2d). */
export const ARCHIVED_VIEW = "archived";

/** The Archived view of one space: `/knowledge?space=<id>&view=archived`. */
export function archivedHref(spaceId: string): string {
  return `${spaceHref(spaceId)}&view=${ARCHIVED_VIEW}`;
}

/** The address of a heading on a page: the page, then `#` and its anchor. */
export function headingHref(pageId: string, slug: string): string {
  return `${pageHref(pageId)}#${encodeURIComponent(slug)}`;
}

/**
 * The Search view for these words and filters (KB-3d). No words and no
 * filter is the bare view. The words come first in the address.
 */
export function searchHref(q = "", filters: SearchFilters = NO_FILTERS): string {
  const pairs: [string, string][] = [];
  if (q.trim()) pairs.push(["q", q]);
  pairs.push(...filtersToPairs(filters));
  if (!pairs.length) return KNOWLEDGE_SEARCH;
  return `${KNOWLEDGE_SEARCH}?${pairs.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&")}`;
}

/** The page id of a Knowledge path, or `null` off a page. */
export function pageIdOf(pathname: string | null): string | null {
  const m = /^\/knowledge\/pages\/([^/?#]+)/.exec(pathname ?? "");
  return m ? decodeURIComponent(m[1]) : null;
}
