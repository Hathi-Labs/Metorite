/**
 * Projects · where a dragged NODE lands — WS-27bk §9.12.4 slice 2.
 *
 * The board already has a drop planner for cards (`lib/boardDrop.ts`), and
 * this reuses its `dropIndexFor` rather than repeating the off-by-one that
 * module exists to own. What is new here is the tree's own two things: a drop
 * has TWO meanings, and the rank maths write a float on `pm_projects`.
 *
 * ── A drop means one of two things ────────────────────────────────────────
 *
 *   ONTO a row      → re-parent into it, at the end of its children
 *   BETWEEN rows    → reorder among those siblings, same parent
 *
 * Both are one `POST /nodes/{id}/move`. The endpoint takes a parent and an
 * optional position, so the difference is which fields this plan fills.
 *
 * ⚠️ **The grammar decides, and it decides HERE too.** A target the server
 * would refuse must not be a drop target at all — `moveRefusal` is consulted
 * before a plan exists, so the cursor says no before the mouse comes up. The
 * server still refuses independently. This is the courtesy in front of it.
 */

import { dropIndexFor } from "@/lib/boardDrop";

import { type ProjectNode, moveRefusal, pathTo } from "./tree";

/** Where a drag is hovering. */
export type TreeDropTarget =
  | { kind: "onto"; nodeId: string }
  | { kind: "between"; parentId: string | null; index: number };

export interface TreeDropPlan {
  parentId: string | null;
  /** Omitted when only the parent changes — the server appends. */
  position?: number;
}

/**
 * The gap between two float positions below which they are re-spread.
 *
 * ⚠️ **Doubles run out.** Repeatedly dropping into the same gap halves it each
 * time, and after roughly fifty insertions in one place the midpoint of two
 * neighbours IS one of them. The order then stops changing and the drag looks
 * broken while every write succeeds.
 */
export const MIN_GAP = 1e-6;

/** What a first, unordered sibling set is spread across. */
export const POSITION_SPAN = 65536;

/** The children of a parent id, in the order the tree draws them. */
export function siblingsOf(
  roots: readonly ProjectNode[],
  parentId: string | null,
): ProjectNode[] {
  if (parentId === null) return [...roots];
  const path = pathTo(roots, parentId);
  const parent = path[path.length - 1];
  return [...(parent?.children ?? [])];
}

function positionOf(node: ProjectNode): number | null {
  return typeof node.position === "number" ? node.position : null;
}

/**
 * Every sibling needs a position before a midpoint means anything.
 *
 * A tree that has never been reordered carries `null` on every row, and the
 * midpoint of two nulls is not a number. So the first drop into such a set
 * spreads the whole set — one write per sibling, once, and never again.
 */
export function needsSpread(siblings: readonly ProjectNode[]): boolean {
  return siblings.some((node) => positionOf(node) === null);
}

/**
 * The position for a node landing at `index` among `others`.
 *
 * `null` means the caller must re-spread first: either a sibling has no
 * position yet, or the gap has closed past `MIN_GAP`.
 */
export function positionAt(
  others: readonly ProjectNode[],
  index: number,
): number | null {
  if (needsSpread(others)) return null;
  const before = index > 0 ? positionOf(others[index - 1]) : null;
  const after = index < others.length ? positionOf(others[index]) : null;

  if (before === null && after === null) return POSITION_SPAN / 2;
  if (before === null) return (after as number) / 2;
  if (after === null) return before + POSITION_SPAN / 2;
  if (after - before < MIN_GAP) return null;
  return (before + after) / 2;
}

/**
 * Positions for a whole sibling set, evenly spread, with the moving node at
 * `index`. The answer when `positionAt` returns `null`.
 */
export function spreadPositions(
  others: readonly ProjectNode[],
  movingId: string,
  index: number,
): Array<{ id: string; position: number }> {
  const order = [
    ...others.slice(0, index).map((n) => n.id),
    movingId,
    ...others.slice(index).map((n) => n.id),
  ];
  const step = POSITION_SPAN / (order.length + 1);
  return order.map((id, i) => ({ id, position: step * (i + 1) }));
}

/**
 * The plan for one drop, or the refusal that stops it.
 *
 * Returns `{ refusal }` when the grammar says no, so a caller can show the
 * same sentence the picker shows. Returns `null` for a drop that changes
 * nothing, which a caller should skip rather than write.
 */
export function planTreeDrop(
  roots: readonly ProjectNode[],
  movingId: string,
  target: TreeDropTarget,
):
  | { plan: TreeDropPlan; spread?: Array<{ id: string; position: number }> }
  | { refusal: string }
  | null {
  const parentId =
    target.kind === "onto" ? target.nodeId : target.parentId;

  const refusal = moveRefusal(roots, movingId, parentId);
  if (refusal) return { refusal };

  if (target.kind === "onto") {
    // Landing INTO a row appends. Asking for a position as well would mean
    // guessing where in a list the user has not looked at, and "at the end"
    // is the one answer that never surprises.
    const path = pathTo(roots, movingId);
    const currentParent =
      path.length > 1 ? path[path.length - 2].id : null;
    if (currentParent === parentId) return null;
    return { plan: { parentId } };
  }

  const siblings = siblingsOf(roots, parentId);
  const index = dropIndexFor(siblings, movingId, target.index);
  const others = siblings.filter((n) => n.id !== movingId);

  // Dropping a node back into the slot it already occupies.
  const from = siblings.findIndex((n) => n.id === movingId);
  if (from !== -1 && others[index - 1]?.id === siblings[from - 1]?.id
      && others[index]?.id === siblings[from + 1]?.id) {
    return null;
  }

  const position = positionAt(others, index);
  if (position === null) {
    const spread = spreadPositions(others, movingId, index);
    const mine = spread.find((row) => row.id === movingId);
    return {
      plan: { parentId, position: mine ? mine.position : POSITION_SPAN / 2 },
      spread,
    };
  }
  return { plan: { parentId, position } };
}
