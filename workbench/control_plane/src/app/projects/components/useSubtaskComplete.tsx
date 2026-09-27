"use client";

/**
 * Projects · complete a parent, and ask about its open subtasks first
 * (D-PM-38 decision 2, Subtasks S5).
 *
 * Every Projects door that moves a status mounts this hook's `dialog` once
 * and routes a status change through `changeStatus`. When the change does
 * not complete a parent with open subtasks, `changeStatus` answers `null`
 * and the door does what it always did.
 *
 * When it does, the hook asks, writes ONE PATCH with `include_subtasks` set
 * to the answer, and shows the receipt through the shared toast: "Completed"
 * or "Completed · and 3 subtasks", with Undo.
 *
 * **What Undo covers.** The parent and every subtask the cascade closed, each
 * put back to its EXACT prior status (D79), and each only while it still
 * holds the status the complete set (`revertCascade`, If-Match). A task
 * somebody moved since keeps that move, and the toast says so. A recurring
 * subtask's next instance, which the complete created, stays.
 */

import { useCompleteSubtasksPrompt } from "@/components/SubtaskCascade";
import { useToast } from "@/components/ui/Toast";
import { useUndo } from "@/components/UndoProvider";
import {
  type CascadeChange,
  completeReceipt,
  revertCascade,
  revertSummary,
} from "@/lib/subtaskCascade";

import { type CascadeReport, type TaskRow, projectsApi } from "../lib/api";
import { type CascadeLane, completionAsk, writeCompletion } from "../lib/cascadeAsk";

export function useSubtaskComplete() {
  const { ask, dialog } = useCompleteSubtasksPrompt();
  const toast = useToast();
  const undoApi = useUndo();

  /**
   * Move `task` to `statusId`, asking first when that completes a parent
   * with open subtasks. `null` means "not asked": the caller writes as it
   * always did. `after` refreshes the caller's surface after every write,
   * Undo and redo included.
   */
  async function changeStatus(
    task: TaskRow,
    statusId: string,
    lanes: readonly CascadeLane[],
    after: () => Promise<void> | void,
    open?: number,
  ): Promise<(TaskRow & CascadeReport) | null> {
    const count = completionAsk(task, statusId, lanes, open);
    if (count === 0) return null;
    const include = await ask(count, task.title);
    // The prior status from the SERVER, read just before the write (D79).
    const io = {
      read: (id: string) => projectsApi.task(id),
      write: (id: string, sid: string, withSubtasks: boolean) =>
        projectsApi.patchTask(id, { status_id: sid }, { includeSubtasks: withSubtasks }),
    };
    const first = await writeCompletion(io, task.id, statusId, include);
    const fresh = first.fresh;
    let changes: CascadeChange[] = first.changes;

    // One state for the toast's Undo and the page's Ctrl+Z, so pressing
    // both does not put anything back twice.
    let applied = true;
    const revert = async () => {
      if (!applied) return;
      applied = false;
      const outcome = await revertCascade(changes, {
        read: (id) => projectsApi.task(id),
        write: (id, prior, ifMatch) =>
          projectsApi.patchTask(id, { status_id: prior }, { ifMatch }),
      });
      const note = revertSummary(outcome);
      if (note) toast.show({ variant: "error", title: note });
      await after();
    };
    const redo = async () => {
      if (applied) return;
      applied = true;
      // The redo reads the server again, and may close children the first
      // write did not, so the next Undo puts back what THIS write moved.
      changes = (await writeCompletion(io, task.id, statusId, include)).changes;
      await after();
    };
    undoApi.record({ label: `completed ${task.title}`, undo: revert, redo });
    toast.show({
      key: `projects:complete:${task.id}`,
      variant: "success",
      title: completeReceipt(fresh.subtasks_completed ?? 0),
      action: { label: "Undo", onClick: () => void revert() },
    });
    await after();
    return fresh;
  }

  return { changeStatus, ask, dialog };
}
