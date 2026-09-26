/**
 * Projects · what the delete confirmation says.
 *
 * **A Projects delete is permanent, so the words say so.** Checked
 * 2026-09-24: `DELETE /projects/tasks/{id}` (`delete_task_in`) and the bulk
 * action `delete` (`bulk.py`) both run `DELETE FROM pm_tasks`. Migration
 * 168's trigger writes a tombstone so a sync client can see the delete, and
 * nothing restores a row from it. There is no Undo toast on this path.
 *
 * Subtasks are PROMOTED, not deleted. They move up ONE level, to the deleted
 * task's own parent (D-PM-38). Both delete paths call
 * `core.lift_subtasks_to_grandparent` first. Until 2026-09-26 the FK's SET NULL
 * made them top-level, and "moved up a level" was false for a subtask of a
 * subtask. `test_projects_routes.py` holds the server half. The copy
 * says that as well, because somebody who expects a cascade would otherwise
 * delete a parent to be rid of a subtree and find the subtree still there.
 *
 * My Tasks has its own copy (`app/tasks/lib/removal.ts` `removalCopy`),
 * because its delete CAN be undone for a few seconds. Both draw through
 * `components/ui/ConfirmDialog.tsx`. Fence: `deleteCopy.test.ts`.
 */

export interface DeleteCopy {
  title: string;
  subject: string | null;
  body: string;
  note: string | null;
  confirmLabel: string;
}

/** One task, from the board, the list or the panel. */
export function deleteTaskCopy(
  task: { title?: string | null; subtasks?: number } | null,
): DeleteCopy {
  const kids = task?.subtasks ?? 0;
  return {
    title: "Delete this task?",
    subject: task?.title ?? null,
    body: "It is deleted for good. This cannot be undone.",
    note: kids
      ? `Its ${kids} subtask${kids === 1 ? " is" : "s are"} kept and moved up a level, not deleted.`
      : null,
    confirmLabel: "Delete",
  };
}

/** The bulk bar's delete, over a selection. */
export function deleteTasksCopy(count: number): DeleteCopy {
  const one = count === 1;
  return {
    title: one ? "Delete 1 task?" : `Delete ${count} tasks?`,
    subject: null,
    body: `${one ? "It is" : "They are"} deleted for good. This cannot be undone.`,
    note: "Any subtasks they have are kept and moved up a level, not deleted.",
    confirmLabel: "Delete",
  };
}
