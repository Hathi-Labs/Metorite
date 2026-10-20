// ── The breadcrumbs of a page (WS-61 KB-2d, `knowledge_base.md` §21.7) ─────
//
// Pure. The trail is the space, then each parent from the root down, then
// the page. A long trail folds its middle into one `…` that opens a list.
// On a phone, every crumb goes into that list, so the row stays one button.

import type { PageSummary } from "./types";

/** One crumb: a space or a page, with its address. */
export interface Crumb {
  id: string;
  title: string;
  href: string;
  kind: "space" | "page";
}

/** The longest trail that shows in full on a wide screen. */
export const MAX_SHOWN_CRUMBS = 3;

/**
 * The parents of a page, from the root of its space down to its parent.
 *
 * It walks `parent_id` through the live pages of the space. A parent that
 * the member cannot see, or that is archived, ends the walk, so the trail
 * shows only what the member can open. A loop in bad data ends it too.
 */
export function parentChain(
  pages: readonly Pick<PageSummary, "id" | "parent_id" | "title">[],
  parentId: string | null,
): Array<Pick<PageSummary, "id" | "title">> {
  const byId = new Map(pages.map((p) => [p.id, p]));
  const chain: Array<Pick<PageSummary, "id" | "title">> = [];
  const seen = new Set<string>();
  let at = parentId;
  while (at && !seen.has(at)) {
    seen.add(at);
    const parent = byId.get(at);
    if (!parent) break;
    chain.unshift({ id: parent.id, title: parent.title });
    at = parent.parent_id;
  }
  return chain;
}

/** What the row draws: the crumbs in view, and the crumbs in the `…` list. */
export interface FoldedCrumbs {
  /** The crumbs before the `…`. */
  head: Crumb[];
  /** The crumbs in the `…` list. Empty: no `…`. */
  folded: Crumb[];
  /** The crumbs after the `…`. */
  tail: Crumb[];
}

/**
 * Fold a trail. On a wide screen a trail of more than three crumbs keeps the
 * first and the last, and the `…` holds the rest. On a phone the `…` holds
 * every crumb.
 */
export function foldCrumbs(trail: readonly Crumb[], phone: boolean): FoldedCrumbs {
  if (phone) return { head: [], folded: trail.length > 1 ? [...trail] : [], tail: trail.length > 1 ? [] : [...trail] };
  if (trail.length <= MAX_SHOWN_CRUMBS) return { head: [...trail], folded: [], tail: [] };
  return {
    head: trail.slice(0, 1),
    folded: trail.slice(1, -1),
    tail: trail.slice(-1),
  };
}
