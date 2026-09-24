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

/**
 * The label on a board task's delete gesture, everywhere in My Tasks.
 *
 * ONE name per act. "Delete" removes my own task for good. "Remove from my
 * lists" takes a board task off my lists, and the board keeps it. "Not mine"
 * and a bare "Remove" were other names for the second act, and are gone.
 *
 * ⚠️ Two words stay on purpose, because they do NOT name a delete:
 * "Eliminate" is the priority matrix's name for a low level (it opens a
 * choice of Someday or the gesture above), and Clarify's "Trash" is the GTD
 * disposition TRASH, which never purges a row.
 */
export const REMOVE_LABEL = "Remove from my lists";
export const DELETE_LABEL = "Delete";
/** A selection that holds both kinds. */
export const MIXED_LABEL = "Delete or remove";

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
  return MIXED_LABEL;
}

/**
 * The seconds the Undo toast holds a delete open (`UndoToast.tsx`). When the
 * toast closes without an Undo, `dismissUndo` purges my own tasks for good.
 */
export const UNDO_WINDOW_SECONDS = 7;

/** What the confirmation says, for the tasks it will act on. */
export interface RemovalCopy {
  title: string;
  /** What happens, stated as it really happens. */
  body: string;
  /** A second fact about the board tasks in the set, when there are some. */
  note: string | null;
  confirmLabel: string;
  icon: "Trash2" | "UserX";
}

/**
 * The confirmation's words, TRUE for each case (checked 2026-09-24):
 *
 * - **My own task** is soft-deleted: my overlay says TRASH, and the Undo
 *   toast restores it (`lensRestoreItem`). When the toast closes, the store
 *   purges it with `DELETE /projects/my/tasks/{id}`, which deletes the row
 *   for good. So "undo for a few seconds, then gone for good" is the truth.
 * - **A board task** only leaves my lists: TRASH on my overlay, never a
 *   purge (`deleteItems`). The Undo toast puts my overlay back. The board
 *   keeps the task, so the copy never says "delete" for it.
 *
 * Fence: `removal.test.ts`.
 */
export function removalCopy(
  items: readonly Pick<MyTask, "projectId">[],
  scope: RemovalScope,
): RemovalCopy {
  const count = items.length;
  const board = items.filter((i) => !canPurge(i, scope)).length;
  const mine = count - board;
  const tasks = count === 1 ? "this task" : `${count} tasks`;
  const undo = `You can undo this for ${UNDO_WINDOW_SECONDS} seconds.`;
  if (mine === 0) {
    const one = count === 1;
    return {
      title: `Remove ${tasks} from your lists?`,
      body:
        `${one ? "It leaves" : "They leave"} your lists. The team board keeps ` +
        `${one ? "it" : "them"}, and nothing is deleted. ${undo}`,
      note: null,
      confirmLabel: REMOVE_LABEL,
      icon: "UserX",
    };
  }
  return {
    title: board ? `Delete or remove ${tasks}?` : `Delete ${tasks}?`,
    body:
      `${undo} After that, ${mine === 1 ? "your task is" : `your ${mine} tasks are`} ` +
      "deleted for good.",
    note: board
      ? `${board} of these ${board === 1 ? "is" : "are"} on a team board. ` +
        `${board === 1 ? "It leaves" : "Those leave"} your lists, and the board ` +
        `keeps ${board === 1 ? "it" : "them"}.`
      : null,
    confirmLabel: board ? MIXED_LABEL : DELETE_LABEL,
    icon: "Trash2",
  };
}
