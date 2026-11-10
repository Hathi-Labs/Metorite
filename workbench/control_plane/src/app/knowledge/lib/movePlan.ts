// ── The Move form of the page menu (WS-61 KB-2d, `knowledge_base.md` §21.7) ─
//
// Pure. The member picks a space and a parent, and the form sends one
// `POST /knowledge/pages/{id}/move`. These functions list the parents a page
// may go under, and build the body of the move. The gateway checks each
// rule again (`pages.move_page`), so a refusal still reads as words.

import { buildTree, type TreeNode } from "./tree";
import type { PageMoveBody, PageSummary } from "./types";

/** The value of the "top of the space" choice. */
export const TOP_LEVEL = "";

/** One parent the page may go under, with its depth in the tree. */
export interface ParentChoice {
  id: string;
  title: string;
  depth: number;
}

/**
 * The pages of a space that may be the new parent, in tree order. The page
 * itself and each page under it are left out, because a page cannot go into
 * its own subtree.
 */
export function parentChoices(pages: readonly PageSummary[], pageId: string): ParentChoice[] {
  const out: ParentChoice[] = [];
  const walk = (nodes: TreeNode[], depth: number) => {
    for (const node of nodes) {
      if (node.page.id === pageId) continue;
      out.push({ id: node.page.id, title: node.page.title, depth });
      walk(node.children, depth + 1);
    }
  };
  walk(buildTree(pages.filter((p) => p.state !== "archived")), 0);
  return out;
}

/** True when the page has a live child in this list. */
export function hasChildren(pages: readonly PageSummary[], pageId: string): boolean {
  return pages.some((p) => p.parent_id === pageId && p.id !== pageId && p.state !== "archived");
}

/**
 * The body of the move, or `null` when the pick is where the page is now.
 * A move to another space names the space and the parent, and the gateway
 * puts the page last among its new siblings.
 */
export function moveRequest(
  page: Pick<PageSummary, "space_id" | "parent_id">,
  spaceId: string,
  parentId: string,
): PageMoveBody | null {
  const parent = parentId === TOP_LEVEL ? null : parentId;
  if (spaceId !== page.space_id) return { space_id: spaceId, parent_id: parent };
  if (parent === (page.parent_id ?? null)) return null;
  return { parent_id: parent };
}
