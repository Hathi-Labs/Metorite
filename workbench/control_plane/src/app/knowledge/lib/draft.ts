// ── The edit draft of one page (WS-61 KB-2a fix round 1) ───────────────────
//
// Pure, apart from the storage handed in.
//
// P2 of the review: a save diffed the draft against the LIVE page, which the
// read cache refreshes behind the editor. A field the member never touched
// could then go back to the server as a change, over a newer version. So an
// edit takes a SNAPSHOT when it begins, the save diffs against the snapshot,
// and a newer version on the server makes the edit stale (`isStale`).
//
// P1 of the review: a dirty draft lived only in component state. The shell
// swaps its whole tree when the window crosses 767px, so the draft went with
// it. A dirty draft now lives in session storage, keyed by the account and
// the page, and the remounted view reads it back. Session storage ends with
// the tab. The key carries the account, so a second member in the same tab
// never reads the first one's text. Save and Discard clear it.

import { patchBody } from "./labels";

/** The page as the edit found it. `version` is its newest version then. */
export interface EditSnapshot {
  pageId: string;
  version: number;
  title: string;
  body_md: string;
}

/** What the member has typed. */
export interface Draft {
  title: string;
  body_md: string;
}

type Versioned = { versions: ReadonlyArray<{ version: number }> };

/** The newest version of a page, or 0 for a page with none. */
export function latestVersion(page: Versioned): number {
  return page.versions.reduce((top, v) => Math.max(top, v.version), 0);
}

export function snapshotOf(page: Versioned & { id: string; title: string; body_md: string }): EditSnapshot {
  return { pageId: page.id, version: latestVersion(page), title: page.title, body_md: page.body_md };
}

/** True when the draft differs from the snapshot. An emptied title counts. */
export function isDirty(snapshot: EditSnapshot, draft: Draft): boolean {
  return draft.title !== snapshot.title || draft.body_md !== snapshot.body_md;
}

/** True when someone saved a newer version after the edit began. */
export function isStale(snapshot: EditSnapshot, live: Versioned): boolean {
  return latestVersion(live) > snapshot.version;
}

/** The fields the member changed, against the SNAPSHOT. `null` for none. */
export function draftPatch(snapshot: EditSnapshot, draft: Draft): { title?: string; body_md?: string } | null {
  return patchBody(snapshot, draft);
}

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function draftKey(scope: string, pageId: string): string {
  return `kb-draft:${scope}:${pageId}`;
}

export function writeDraft(store: Store, scope: string, snapshot: EditSnapshot, draft: Draft): void {
  try {
    store.setItem(draftKey(scope, snapshot.pageId), JSON.stringify({ snapshot, draft }));
  } catch {
    // A full or blocked store keeps the draft in memory only.
  }
}

/** The stored draft of this page for this account, or `null`. */
export function readDraft(
  store: Store,
  scope: string,
  pageId: string,
): { snapshot: EditSnapshot; draft: Draft } | null {
  let raw: string | null = null;
  try {
    raw = store.getItem(draftKey(scope, pageId));
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { snapshot?: Partial<EditSnapshot>; draft?: Partial<Draft> };
    const s = parsed.snapshot;
    const d = parsed.draft;
    if (
      !s || !d || s.pageId !== pageId || typeof s.version !== "number" ||
      typeof s.title !== "string" || typeof s.body_md !== "string" ||
      typeof d.title !== "string" || typeof d.body_md !== "string"
    ) {
      return null;
    }
    return {
      snapshot: { pageId: s.pageId, version: s.version, title: s.title, body_md: s.body_md },
      draft: { title: d.title, body_md: d.body_md },
    };
  } catch {
    return null;
  }
}

export function clearDraft(store: Store, scope: string, pageId: string): void {
  try {
    store.removeItem(draftKey(scope, pageId));
  } catch {
    // Nothing to clear.
  }
}
