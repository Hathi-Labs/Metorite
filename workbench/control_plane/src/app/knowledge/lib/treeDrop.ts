// ── Where a dragged page lands (WS-61 KB-2e, `knowledge_base.md` §21.5) ────
//
// Pure. The rail turns the pointer into a `PageDropTarget`, and this file
// turns the target into the body of `POST /knowledge/pages/{id}/move`.
//
// A drop means one of two things:
//
//   ONTO a row      → the page goes under that row, last among its children
//   BETWEEN rows    → the page goes to that place among those siblings
//
// `position` is the 0-based place among the NEW siblings, without the page
// itself. The gateway counts it among the siblings the member sees, in the
// order of `compareSiblings`, so the client and the server count the same
// rows (`pages._place`).
//
// ⚠️ A page cannot drop into its own subtree. The gateway refuses it too
// (`pages._move_parent`). Here it is no drop target at all, so the pointer
// says no before the mouse comes up.

import { dropIndexFor } from "@/lib/boardDrop";

import { buildTree, type TreeNode } from "./tree";
import type { PageMoveBody, PageSummary } from "./types";

/** Where a drag hovers. `index` is a gap among the siblings as drawn. */
export type PageDropTarget =
  | { kind: "onto"; pageId: string }
  | { kind: "between"; parentId: string | null; index: number };

/** What a drop asks of the gateway, or why there is nothing to ask. */
export type PageDropPlan =
  | { move: PageMoveBody }
  | { refusal: "own-subtree" | "unknown" }
  | { nothing: true };

/**
 * Which part of a row the pointer is in. The middle band is the biggest,
 * because "under this page" is the act members mean most often. Each edge
 * band is a quarter of the row, as in the Projects tree.
 */
export function dropZoneAt(offsetY: number, height: number): "before" | "onto" | "after" {
  if (height <= 0) return "onto";
  const edge = height * 0.25;
  if (offsetY < edge) return "before";
  if (offsetY > height - edge) return "after";
  return "onto";
}

/**
 * The target a zone of a row means. `index` is the row's place among its
 * siblings. On an OPEN row with children, the bottom edge sits just above
 * the first child, so it means "first child". A line there must not mean
 * "after this whole branch", which shows far below it.
 */
export function targetFor(
  zone: "before" | "onto" | "after",
  pageId: string,
  parentId: string | null,
  index: number,
  open = false,
): PageDropTarget {
  if (zone === "onto") return { kind: "onto", pageId };
  if (zone === "after" && open) return { kind: "between", parentId: pageId, index: 0 };
  return { kind: "between", parentId, index: zone === "before" ? index : index + 1 };
}

/** The ids of `pageId` and each page under it, in the tree as drawn. */
export function subtreeIds(roots: readonly TreeNode[], pageId: string): Set<string> {
  const out = new Set<string>();
  const find = (nodes: readonly TreeNode[]): TreeNode | null => {
    for (const node of nodes) {
      if (node.page.id === pageId) return node;
      const hit = find(node.children);
      if (hit) return hit;
    }
    return null;
  };
  const walk = (node: TreeNode) => {
    out.add(node.page.id);
    node.children.forEach(walk);
  };
  const start = find(roots);
  if (start) walk(start);
  return out;
}

/** The children of `parentId` as drawn. `null` is the root of the space. */
function drawnChildren(roots: readonly TreeNode[], parentId: string | null): TreeNode[] {
  if (parentId === null) return [...roots];
  const find = (nodes: readonly TreeNode[]): TreeNode | null => {
    for (const node of nodes) {
      if (node.page.id === parentId) return node;
      const hit = find(node.children);
      if (hit) return hit;
    }
    return null;
  };
  return [...(find(roots)?.children ?? [])];
}

/**
 * The move a drop asks for.
 *
 * The drawn root can hold a page whose parent the member cannot see
 * (`lib/tree.ts`). Such a page is not a sibling for the gateway, so the
 * place counts only the drawn rows whose `parent_id` is the new parent.
 */
export function planDrop(
  pages: readonly PageSummary[],
  movingId: string,
  target: PageDropTarget,
): PageDropPlan {
  const moving = pages.find((p) => p.id === movingId);
  if (!moving) return { refusal: "unknown" };
  const roots = buildTree(pages);
  const own = subtreeIds(roots, movingId);
  const parentId = target.kind === "onto" ? target.pageId : target.parentId;
  if (parentId !== null && own.has(parentId)) return { refusal: "own-subtree" };
  if (parentId !== null && !pages.some((p) => p.id === parentId)) return { refusal: "unknown" };

  const drawn = drawnChildren(roots, parentId);
  const gap = target.kind === "onto" ? drawn.length : Math.max(0, Math.min(target.index, drawn.length));
  // The gap among the drawn rows, without the moving page.
  const at = dropIndexFor(
    drawn.map((n) => n.page),
    movingId,
    gap,
  );
  const others = drawn.filter((n) => n.page.id !== movingId);
  const real = (n: TreeNode) => (n.page.parent_id ?? null) === parentId;
  const position = others.slice(0, at).filter(real).length;

  const sameParent = (moving.parent_id ?? null) === parentId;
  if (sameParent) {
    const now = others.filter(real).length;
    const was = drawn.filter(real).findIndex((n) => n.page.id === movingId);
    // The page is already at that place, or an ONTO its own parent while it is last.
    if (was === position || (target.kind === "onto" && was === now)) return { nothing: true };
    return { move: { position } };
  }
  return { move: { parent_id: parentId, position } };
}

/**
 * The list as it reads after a move, so the rail draws the drop at once.
 * The gateway's answer replaces it on the next read. It numbers the drawn
 * siblings of the new parent 0 to n. A hidden sibling is not in the list.
 */
export function applyMove(
  pages: readonly PageSummary[],
  movingId: string,
  move: PageMoveBody,
): PageSummary[] {
  const moving = pages.find((p) => p.id === movingId);
  if (!moving) return [...pages];
  const parentId = "parent_id" in move ? (move.parent_id ?? null) : (moving.parent_id ?? null);
  const siblings = drawnChildren(buildTree(pages), parentId)
    .map((n) => n.page)
    .filter((p) => (p.parent_id ?? null) === parentId && p.id !== movingId);
  const at = Math.min(move.position ?? siblings.length, siblings.length);
  const order = [...siblings.slice(0, at), { ...moving, parent_id: parentId }, ...siblings.slice(at)];
  const placed = new Map(order.map((p, i) => [p.id, { ...p, position: i }]));
  return pages.map((p) => placed.get(p.id) ?? p);
}
