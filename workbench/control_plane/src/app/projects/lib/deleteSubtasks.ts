/**
 * Projects · which subtasks go with a deleted task (D-OP-10, OP-7).
 *
 * The delete dialog draws the task's subtasks as an indented tree of check
 * boxes. This file is the state of that tree, with no React in it, so a test
 * can hold the rules:
 *
 * - Every box is CLEAR at first, so the default is the old behaviour (D-PM-38:
 *   each subtask moves up one level).
 * - A tick on a parent ticks its whole subtree. The member can clear any of
 *   them again.
 * - A clear on a parent clears NOTHING below it. A clear on a child leaves
 *   its parent ticked.
 * - A kept row under a ticked ancestor "moves up". The server moves it to its
 *   nearest kept ancestor, or to the parent of the deleted task
 *   (`delete_task_in`, `kept_parents`).
 * - The task itself is always in the count.
 *
 * The server sends only the subtasks the caller can see. A row whose parent is
 * hidden is drawn one level below the task, where the member sees it.
 * Fence: `deleteSubtasks.test.ts`.
 */

import type { SubtreeRow } from "./api";

export interface TreeRow {
  id: string;
  title: string;
  /** The lane's name, or "" when the row has none. */
  status: string;
  archived: boolean;
  /** 1 for a direct subtask. */
  depth: number;
  /** Ids of the visible rows directly below this one. */
  children: string[];
  /** Every visible row below this one, at any depth. */
  descendants: string[];
  /** The visible parent, or null for a row drawn directly under the task. */
  parentId: string | null;
}

/** The server's rows as a depth-first list, so a child follows its parent. */
export function buildRows(rows: SubtreeRow[]): TreeRow[] {
  const ids = new Set(rows.map((r) => r.id));
  const kids = new Map<string | null, SubtreeRow[]>();
  for (const r of rows) {
    const key = r.parent_task_id && ids.has(r.parent_task_id) ? r.parent_task_id : null;
    const list = kids.get(key) ?? [];
    list.push(r);
    kids.set(key, list);
  }
  const out: TreeRow[] = [];
  const seen = new Set<string>();
  const walk = (parent: string | null, depth: number): string[] => {
    const below: string[] = [];
    for (const r of kids.get(parent) ?? []) {
      if (seen.has(r.id)) continue;
      seen.add(r.id);
      const row: TreeRow = {
        id: r.id,
        title: r.title,
        status: r.status_name ?? "",
        archived: r.archived,
        depth,
        children: (kids.get(r.id) ?? []).map((c) => c.id),
        descendants: [],
        parentId: parent,
      };
      out.push(row);
      row.descendants = walk(r.id, depth + 1);
      below.push(r.id, ...row.descendants);
    }
    return below;
  };
  walk(null, 1);
  return out;
}

/** A tick on a clear box ticks it and its subtree. A tick on a ticked box
 *  clears that box alone. */
export function toggleRow(ticked: ReadonlySet<string>, rows: TreeRow[], id: string): Set<string> {
  const next = new Set(ticked);
  if (next.has(id)) {
    next.delete(id);
    return next;
  }
  next.add(id);
  for (const d of rows.find((r) => r.id === id)?.descendants ?? []) next.add(d);
  return next;
}

/** "Keep all" is an empty set. "Delete all" is every visible row. */
export function setAll(rows: TreeRow[], on: boolean): Set<string> {
  return new Set(on ? rows.map((r) => r.id) : []);
}

/** True for a kept row that has a ticked ancestor: it moves up. */
export function movesUp(ticked: ReadonlySet<string>, rows: TreeRow[], id: string): boolean {
  if (ticked.has(id)) return false;
  const byId = new Map(rows.map((r) => [r.id, r]));
  for (let cur = byId.get(id)?.parentId ?? null; cur; cur = byId.get(cur)?.parentId ?? null) {
    if (ticked.has(cur)) return true;
  }
  return false;
}

export interface DeleteCounts {
  /** The task and each ticked subtask. */
  deletes: number;
  /** The visible subtasks that stay. */
  keeps: number;
}

export function countsOf(ticked: ReadonlySet<string>, rows: TreeRow[]): DeleteCounts {
  const marked = rows.filter((r) => ticked.has(r.id)).length;
  return { deletes: 1 + marked, keeps: rows.length - marked };
}

/** The ids to send, in the tree's order. */
export function idsToDelete(ticked: ReadonlySet<string>, rows: TreeRow[]): string[] {
  return rows.filter((r) => ticked.has(r.id)).map((r) => r.id);
}

const tasks = (n: number) => `${n} task${n === 1 ? "" : "s"}`;

export function summaryLine({ deletes, keeps }: DeleteCounts): string {
  return `Deletes ${tasks(deletes)} · keeps ${keeps}`;
}

export function confirmLabel({ deletes }: DeleteCounts): string {
  return `Delete ${tasks(deletes)}`;
}

/** The sentence under the heading. */
export function introLine(subtasks: number): string {
  return `This task has ${subtasks} subtask${subtasks === 1 ? "" : "s"}. Mark the ones to delete. The others stay, and move up one level.`;
}

/** What the toast adds when the delete kept subtasks the member cannot see. */
export function keptHiddenNote(n: number): string {
  if (n <= 0) return "";
  return `${n} subtask${n === 1 ? "" : "s"} you cannot see ${n === 1 ? "was" : "were"} kept.`;
}

/** The toast after a delete, from the route's own counts. */
export function afterDeleteTitle(cascaded: {
  subtasks_promoted: number;
  subtasks_deleted?: number;
  subtasks_kept_hidden?: number;
}): string {
  const deleted = cascaded.subtasks_deleted ?? 0;
  const moved = cascaded.subtasks_promoted;
  const parts: string[] = [];
  if (deleted) parts.push(`${deleted} subtask${deleted === 1 ? "" : "s"} deleted too.`);
  if (moved) parts.push(`${moved} subtask${moved === 1 ? "" : "s"} moved up a level.`);
  const hidden = keptHiddenNote(cascaded.subtasks_kept_hidden ?? 0);
  if (hidden) parts.push(hidden);
  return parts.length ? `Deleted. ${parts.join(" ")}` : "Task deleted";
}
