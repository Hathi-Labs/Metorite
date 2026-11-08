/**
 * Projects · the rail's Archived section.
 *
 * Archiving stamps a whole subtree (`archive_node`), so an archived row is
 * normally the head of a filed branch. The rail used to draw those rows
 * inline, among live work. The convention in ClickUp, Linear and Notion is
 * the reverse: filed work leaves the live tree and waits in one collapsed
 * "Archived" section at the foot. This module is the split, and nothing else.
 * It computes no count of work, only of rows.
 *
 * ⚠️ A subproject archived on its own has a live parent. It leaves the live
 * tree and heads its own entry in the section, with the parent's names as the
 * path, so a member can still tell where it came from.
 */
import type { ProjectRow } from "./api";
import { generationOf, nodeKind } from "./tree";

/** The fold id of the rail's Archived section (memory-only, like every fold). */
export const ARCHIVED_FOLD_ID = "__archived__";

export interface ArchivedEntry {
  /** The archived row, with its own children kept (they are filed with it). */
  node: ProjectRow;
  /** Project generations ABOVE this node in the full tree. Feeds `Node`. */
  parentGen: number;
  /** Names of the ancestors, nearest last. Empty for a filed space. */
  path: string[];
}

export interface ArchiveSplit {
  /** The tree with every archived branch removed. */
  active: ProjectRow[];
  /** The heads of the archived branches, in tree order. */
  archived: ArchivedEntry[];
  /** Every archived row, heads and the rows under them. */
  count: number;
}

function isArchived(node: ProjectRow): boolean {
  return Boolean(node.archived_at);
}

function countAll(nodes: readonly ProjectRow[]): number {
  let total = 0;
  for (const node of nodes) total += 1 + countAll(node.children ?? []);
  return total;
}

/** Archived entries that share one parent path, in tree order. */
export interface ArchivedGroup {
  /** The ancestor names, nearest last. Empty for filed spaces. */
  path: string[];
  entries: ArchivedEntry[];
}

/**
 * The Archived section's rows, grouped by where they came from. The section
 * prints each group's path once, above its rows, so a member can tell where
 * a filed project lived. Siblings sit next to each other in tree order, so
 * one pass groups them.
 */
export function groupByPath(entries: readonly ArchivedEntry[]): ArchivedGroup[] {
  const groups: ArchivedGroup[] = [];
  for (const entry of entries) {
    const last = groups[groups.length - 1];
    if (last && samePath(last.path, entry.path)) last.entries.push(entry);
    else groups.push({ path: entry.path, entries: [entry] });
  }
  return groups;
}

function samePath(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((name, i) => name === b[i]);
}

export function splitArchived(roots: readonly ProjectRow[]): ArchiveSplit {
  const archived: ArchivedEntry[] = [];
  let count = 0;

  const walk = (nodes: readonly ProjectRow[], path: string[]): ProjectRow[] => {
    const kept: ProjectRow[] = [];
    for (const node of nodes) {
      if (isArchived(node)) {
        const gen = generationOf(roots, node.id);
        archived.push({
          node,
          parentGen: nodeKind(node) === "folder" ? gen : gen - 1,
          path,
        });
        count += 1 + countAll(node.children ?? []);
        continue;
      }
      const children = node.children ?? [];
      const live = walk(children, [...path, node.name]);
      // ⚠️ Compare each child, not the count. A filed GRANDCHILD leaves the
      // count of this node's own children unchanged, but its parent comes back
      // as a new object. Keeping `node` then kept the old parent too, with the
      // filed row inside it, so the rail drew that row twice (ARC-BUG-1).
      const same = live.length === children.length && live.every((child, i) => child === children[i]);
      kept.push(same ? node : { ...node, children: live });
    }
    return kept;
  };

  const active = walk(roots, []);
  return { active, archived, count };
}
