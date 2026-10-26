/**
 * Every node a task may land in, flattened in tree order — the destination
 * list of the Move dialog, and since S6g of My Tasks' Clarify Where picker and
 * capture chip too (my_tasks_cutover.md §5 S6g). One list, so the doors cannot
 * disagree about which rows are pickable.
 *
 * A folder holds projects, not tasks. The server refuses it with those words,
 * and the list says so first (`legal: false`) rather than teaching by 422. It
 * stays IN the list, because it explains the indent of the project beneath it.
 *
 * A team SPACE holds no tasks either (2026-10-26, `core.assert_holds_tasks`).
 * It shows a dashboard and no board, so a task filed there sits where no view
 * draws it. A personal root is different: it holds the member's Inbox, so a
 * node with `personal: true` stays legal at the top.
 */

import { type ProjectNode, nodeKind, nodeLevel } from "./tree";

export interface DestinationRow {
  node: ProjectNode;
  depth: number;
  legal: boolean;
}

/**
 * True when a node at `depth` is a team space, which holds no tasks. A space
 * is a ROOT (`parent_project_id` empty), not merely a top row: a project whose
 * parent the member cannot see also draws at the top, and it holds tasks.
 */
export function isTeamSpace(node: ProjectNode & { personal?: boolean }, depth: number): boolean {
  return (
    depth === 0 &&
    !node.parent_project_id &&
    nodeKind(node) === "project" &&
    node.personal !== true
  );
}

export function destinations(
  roots: readonly ProjectNode[],
  depth = 0,
): DestinationRow[] {
  const out: DestinationRow[] = [];
  for (const node of roots) {
    out.push({
      node,
      depth,
      legal: nodeLevel(nodeKind(node), depth) !== "folder" && !isTeamSpace(node, depth),
    });
    if (node.children?.length) out.push(...destinations(node.children, depth + 1));
  }
  return out;
}

/** A node's display name, for naming the two ends of a mapping. */
export function nameOf(rows: readonly { node: ProjectNode }[], id: string | null): string {
  if (!id) return "";
  return rows.find((row) => row.node.id === id)?.node.name ?? "";
}
