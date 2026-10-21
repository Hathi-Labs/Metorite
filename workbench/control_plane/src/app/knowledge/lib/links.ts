// ── The addresses of Knowledge (WS-61 KB-2a and KB-3b, `knowledge_base.md` §17) ─
//
// `/knowledge` is the Spaces view. `?space=<id>` selects a space.
// `/knowledge/pages/<id>` is one page, and a search hit opens there.
// `/knowledge/search?q=<words>` is the Search view, so a search is a link.
// `/knowledge?space=<id>&view=archived` is the archived pages of a space (KB-2d).

export const KNOWLEDGE_HOME = "/knowledge";

/** The path of the Search view, with no query. */
export const KNOWLEDGE_SEARCH = `${KNOWLEDGE_HOME}/search`;

export function spaceHref(spaceId: string | null): string {
  return spaceId ? `${KNOWLEDGE_HOME}?space=${encodeURIComponent(spaceId)}` : KNOWLEDGE_HOME;
}

export function pageHref(pageId: string, opts: { edit?: boolean } = {}): string {
  const base = `${KNOWLEDGE_HOME}/pages/${encodeURIComponent(pageId)}`;
  return opts.edit ? `${base}?edit=1` : base;
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

/** The Search view for these words. No words, or only spaces, is the bare view. */
export function searchHref(q = ""): string {
  return q.trim() ? `${KNOWLEDGE_SEARCH}?q=${encodeURIComponent(q)}` : KNOWLEDGE_SEARCH;
}

/** The page id of a Knowledge path, or `null` off a page. */
export function pageIdOf(pathname: string | null): string | null {
  const m = /^\/knowledge\/pages\/([^/?#]+)/.exec(pathname ?? "");
  return m ? decodeURIComponent(m[1]) : null;
}
