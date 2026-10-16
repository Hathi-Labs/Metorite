// ── The addresses of Knowledge (WS-61 KB-2a, `knowledge_base.md` §17) ───────
//
// `/knowledge` is the Spaces view. `?space=<id>` selects a space.
// `/knowledge/pages/<id>` is one page, and KR-3 deep-links a search hit there.

export const KNOWLEDGE_HOME = "/knowledge";

export function spaceHref(spaceId: string | null): string {
  return spaceId ? `${KNOWLEDGE_HOME}?space=${encodeURIComponent(spaceId)}` : KNOWLEDGE_HOME;
}

export function pageHref(pageId: string, opts: { edit?: boolean } = {}): string {
  const base = `${KNOWLEDGE_HOME}/pages/${encodeURIComponent(pageId)}`;
  return opts.edit ? `${base}?edit=1` : base;
}

/** The page id of a Knowledge path, or `null` off a page. */
export function pageIdOf(pathname: string | null): string | null {
  const m = /^\/knowledge\/pages\/([^/?#]+)/.exec(pathname ?? "");
  return m ? decodeURIComponent(m[1]) : null;
}
