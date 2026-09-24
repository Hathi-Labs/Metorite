/**
 * Every node a task may land in, flattened in tree order — the destination
 * list of the Move dialog, and since S6g of My Tasks' Clarify Where picker and
 * capture chip too (my_tasks_cutover.md §5 S6g). One list, so the doors cannot
 * disagree about which rows are pickable.
 *
 * A folder holds projects, not tasks. The server refuses it with those words,
 * and the list says so first (`legal: false`) rather than teaching by 422. It
 * stays IN the list, because it explains the indent of the project beneath it.
 */

import { type ProjectNode, nodeKind, nodeLevel } from "./tree";

export interface DestinationRow {
  node: ProjectNode;
  depth: number;
  legal: boolean;
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
      legal: nodeLevel(nodeKind(node), depth) !== "folder",
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
