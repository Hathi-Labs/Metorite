/**
 * My Tasks · what "delete" may do to a task (my_tasks_cutover.md §5 S6g, P0).
 *
 * **The rule: My Tasks never hard-deletes a task that is not in my personal
 * tree.**
 *
 * My Tasks' delete is soft: my overlay says TRASH, and Undo takes it back.
 * When the Undo window closes, the store PURGES the row. Before S6g the purge
 * was `DELETE /projects/tasks/{id}`, which admits anybody who can see the
 * task. So a member who deleted a task a colleague had assigned from a board
 * removed it for the whole team, for good.
 *
 * Now a board task is REMOVED FROM MY LISTS: my overlay says TRASH, and the
 * board keeps the task. Only a task in my personal root or one of my Areas can
 * be purged. Three fences hold the rule:
 *
 * 1. `taskStore.deleteItems` splits the gesture by `canPurge`, so a board
 *    task never enters `softDeletedIds`;
 * 2. `purgeable` refuses a purge before any request, for any id whose row is
 *    not personal;
 * 3. the purge goes to `DELETE /projects/my/tasks/{id}`, which answers 409 for
 *    a task outside my tree (`personal.purge_my_task`).
 *
 * Fence: `removal.test.ts`.
 */

import { isPersonalTask } from "./clarify";
import type { MyTask } from "./types";

/** My root and my Areas: what decides whether a task is mine to purge. */
export interface RemovalScope {
  personalRootId: string | null;
  areaIds: readonly string[];
}

/**
 * Whether My Tasks may hard-delete this task. `isPersonalTask` is the one
 * rule. A null root fails closed for a task with a project, because then the
 * task cannot be proven mine.
 */
export function canPurge(item: Pick<MyTask, "projectId">, scope: RemovalScope): boolean {
  return isPersonalTask(item, scope.personalRootId, scope.areaIds);
}

/**
 * The ids a purge may send, from the rows the snapshot held. An id with no
 * row, or a row that is not personal, is refused.
 */
export function purgeable(
  ids: readonly string[],
  rows: readonly Pick<MyTask, "id" | "projectId">[],
  scope: RemovalScope,
): { allowed: string[]; refused: string[] } {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const allowed: string[] = [];
  const refused: string[] = [];
  for (const id of ids) {
    const row = byId.get(id);
    if (row && canPurge(row, scope)) allowed.push(id);
    else refused.push(id);
  }
  return { allowed, refused };
}

/** The label on a board task's delete gesture, everywhere in My Tasks. */
export const REMOVE_LABEL = "Remove from my lists";
export const DELETE_LABEL = "Delete";

/** The gesture's label for one task. */
export function removalLabel(item: Pick<MyTask, "projectId">, scope: RemovalScope): string {
  return canPurge(item, scope) ? DELETE_LABEL : REMOVE_LABEL;
}

/** The gesture's label for a selection: all mine, all board, or both. */
export function removalLabelFor(
  items: readonly Pick<MyTask, "projectId">[],
  scope: RemovalScope,
): string {
  const mine = items.filter((i) => canPurge(i, scope)).length;
  if (mine === items.length) return DELETE_LABEL;
  if (mine === 0) return REMOVE_LABEL;
  return "Delete or remove";
}
