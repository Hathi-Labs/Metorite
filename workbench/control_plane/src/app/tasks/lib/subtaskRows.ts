/**
 * My Tasks — each subtask once, with its parent named (D-PM-38, Subtasks S4).
 *
 * My Tasks is always Separate and has no setting (§12.9). A subtask assigned
 * to me is MY work, so it is its own row. Two rules decide how it draws:
 *
 * - **Its parent is in the same list and group** → it draws indented under the
 *   parent, with the nested-row mark, and ONCE. Before S4 it drew twice: as its
 *   own row, and again under the parent when the parent was expanded.
 * - **Its parent is not** → it draws at the top level with the "↳ Parent"
 *   crumb (decision 3: a subtask shown alone names its parent).
 *
 * The parent's expander then lists only the steps NOT already on the list:
 * other people's steps, and mine in another view. They draw muted, because
 * they are context and not my work here.
 *
 * The tree is `@/lib/taskTree` — the one tree model every nesting view uses.
 * This file only adapts `MyTask` to it (`parentItemId` is the parent) and says
 * which row carries a crumb.
 *
 * Fence: `subtaskRows.test.ts`.
 */

import { taskRef } from "@/app/projects/lib/card";
import type { ParentFact } from "@/lib/taskCard";
import { treeRows } from "@/lib/taskTree";

import type { MyTask } from "./types";

/**
 * The parent fact a row carries, built from a parent the store already holds
 * — the shape the gateway's `attach_parent_context` sends, so a row filed on
 * this screen draws the same crumb it draws after a reload.
 */
export function parentFactOf(parent: MyTask): ParentFact {
  return {
    id: parent.id,
    ref: taskRef({ task_number: parent.taskNumber ?? null }),
    title: parent.title,
    archived: Boolean(parent.archivedAt),
  };
}

export interface MyTaskRow {
  item: MyTask;
  /** 0 at the top level, +1 for each parent above it in this set. */
  depth: number;
  /** Draws "↳ Parent": a subtask at the top level, its parent not here. */
  crumb: boolean;
}

/**
 * One list (or one group of it) → the rows it draws: each subtask once,
 * under its parent when the parent is in the same set. The incoming order is
 * kept within a level, because the caller already sorted the list.
 */
export function myTaskRows(items: readonly MyTask[]): MyTaskRow[] {
  const shaped = items.map((item) => ({
    id: item.id,
    parent_task_id: item.parentItemId ?? null,
    item,
  }));
  return treeRows(shaped).map((row) => ({
    item: row.task.item,
    depth: row.depth,
    crumb: row.depth === 0 && Boolean(row.task.item.parentItemId),
  }));
}

/**
 * How many of a parent's steps are NOT on the list, so its expander has
 * something to show. `shown` is every task id the list draws, in any group.
 * The parent's `subtaskCount` counts the children the member can see that are
 * not archived (D-PM-38), and the list holds only mine, so the difference is
 * the steps the expander lists.
 */
export function stepsNotShown(
  parent: MyTask,
  items: readonly MyTask[],
): number {
  const here = items.filter((i) => i.parentItemId === parent.id).length;
  return Math.max(0, (parent.subtaskCount ?? 0) - here);
}

/** The steps an expanded parent lists: the ones not already on the list. */
export function otherSteps(
  children: readonly MyTask[],
  shown: ReadonlySet<string>,
): MyTask[] {
  return children.filter((c) => !shown.has(c.id));
}
