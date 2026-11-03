"use client";

/**
 * UNDO-1 — binds `lib/taskUndo.ts` to the page's undo stack and the toast.
 *
 * A door calls `edited(...)` after its write landed. The member sees the
 * existing toast with an Undo action, and Ctrl+Z reverts the same step.
 * `after` refreshes that door's surface once a revert or redo has run.
 */

import { useToast } from "@/components/ui/Toast";
import { useUndo } from "@/components/UndoProvider";

import { type TaskRow, projectsApi } from "../lib/api";
import {
  type EditIo,
  recordAssigneesEdit,
  recordTaskEdit,
  taskVersions,
} from "../lib/taskUndo";

export const taskEditIo: EditIo = {
  patch: (id, payload, ifMatch) => projectsApi.patchTask(id, payload, { ifMatch }),
  setAssignees: (id, list) => projectsApi.setAssignees(id, list),
  read: (id) => projectsApi.task(id),
  versions: taskVersions,
};

export function useTaskUndo() {
  const undoApi = useUndo();
  const toast = useToast();
  const hooksFor = (after: () => Promise<void> | void) => ({
    record: undoApi.record,
    notify: (message: string) => toast.show({ variant: "error", title: message }),
    after,
  });
  // ⚠️ A door that already raised a toast for this write passes its key, so
  // the Undo toast REPLACES that one in place (fix round 1, finding 5). Two
  // success toasts for one act said the same thing twice.
  const offer = (taskId: string, title: string, undo: () => Promise<void>, key?: string) =>
    toast.show({
      key: key ?? `projects:undo:${taskId}`,
      variant: "success",
      title,
      action: {
        label: "Undo",
        onClick: () =>
          void undo().catch((e: Error) =>
            toast.show({ variant: "error", title: String(e.message) }),
          ),
      },
    });

  return {
    /**
     * A PATCH of `patch` landed on `before`; `fresh` is what it returned.
     * `toastKey` is the key of the toast the door already raised for this
     * write, if any. The Undo toast takes its place.
     */
    edited(
      before: TaskRow,
      patch: Record<string, unknown>,
      fresh: { updated_at?: string | null } | null,
      opts: {
        label: string;
        receipt: string;
        after: () => Promise<void> | void;
        toastKey?: string;
      },
    ) {
      const undo = recordTaskEdit(taskEditIo, hooksFor(opts.after), {
        before,
        patch,
        version: fresh?.updated_at ?? null,
        label: opts.label,
      });
      offer(before.id, opts.receipt, undo, opts.toastKey);
    },
    assigneesEdited(
      id: string,
      before: readonly string[],
      next: readonly string[],
      opts: { label: string; after: () => Promise<void> | void },
    ) {
      const undo = recordAssigneesEdit(taskEditIo, hooksFor(opts.after), {
        id,
        before,
        next,
        label: opts.label,
      });
      offer(id, "Assignees changed", undo);
    },
  };
}
