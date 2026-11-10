// ── The draft of an editor that is still on screen (WS-61 KB-2b fix) ───────
//
// The editor writes its Markdown after a pause in the typing. The shell
// swaps its whole tree when the window crosses 767 px, and React renders the
// new tree BEFORE the old one unmounts. So the new page view read session
// storage before the old editor could write its last keys there.
//
// Each page view in an edit puts a writer here, keyed by its page. A new
// view of the same page calls it before it reads the stored draft. The
// writer touches session storage only, and never React state.

const live = new Map<string, () => void>();

/** Register the writer of one page. The return value removes it. */
export function registerLiveDraft(pageId: string, write: () => void): () => void {
  live.set(pageId, write);
  return () => {
    if (live.get(pageId) === write) live.delete(pageId);
  };
}

/** Let the editor of this page that is still on screen store its draft now. */
export function flushLiveDraft(pageId: string): void {
  live.get(pageId)?.();
}
