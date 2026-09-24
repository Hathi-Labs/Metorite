"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect } from "react";
import { useTaskStore } from "../lib/taskStore";
import { useRemoval } from "../lib/useRemoval";

/**
 * "Are you sure?" confirmation for deletes with real consequences.
 *
 * Store-driven: any delete site calls `requestDelete(ids)`, which opens this
 * dialog for ClickUp-synced or already-clarified tasks (fresh inbox captures
 * skip it). Confirming runs the soft-delete + undo flow; for a synced task the
 * copy warns that the ClickUp task will be ARCHIVED (not hard-deleted — ClickUp
 * keeps it recoverable) once the undo window passes.
 */
export function DeleteConfirmModal() {
  const pendingDeleteIds = useTaskStore((s) => s.pendingDeleteIds);
  const items = useTaskStore((s) => s.items);
  const confirmPendingDelete = useTaskStore((s) => s.confirmPendingDelete);
  const cancelPendingDelete = useTaskStore((s) => s.cancelPendingDelete);
  // S6g, P0 — a board task is removed from my lists, never deleted.
  const removal = useRemoval();

  useEffect(() => {
    if (!pendingDeleteIds) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancelPendingDelete();
      if (e.key === "Enter") confirmPendingDelete();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [pendingDeleteIds, cancelPendingDelete, confirmPendingDelete]);

  if (!pendingDeleteIds?.length) return null;

  const targets = pendingDeleteIds
    .map((id) => items.find((i) => i.id === id))
    .filter((t): t is NonNullable<typeof t> => !!t);
  const count = targets.length;
  const boardCount = targets.filter((t) => !removal.canPurge(t)).length;
  const onlyBoard = boardCount === count;
  const onlyTitle = count === 1 ? targets[0]?.title : null;
  const verb = onlyBoard ? "Remove" : "Delete";

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center p-4 bg-black/40"
      onClick={cancelPendingDelete}
    >
      <div
        className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-sm"
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
      >
        <div className="flex items-start gap-3 px-4 py-4">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
            <Icon name="Trash2" className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-foreground">
              {onlyBoard
                ? count === 1
                  ? "Remove this task from your lists?"
                  : `Remove ${count} tasks from your lists?`
                : count === 1
                  ? "Delete this task?"
                  : `Delete ${count} tasks?`}
            </h2>
            {onlyTitle && (
              <p className="mt-1 truncate text-xs text-muted-foreground">
                &ldquo;{onlyTitle}&rdquo;
              </p>
            )}
            <p className="mt-2 text-xs text-muted-foreground">
              You&rsquo;ll be able to undo this for a few seconds.
            </p>
            {boardCount > 0 && (
              <div className="mt-2 flex items-start gap-1.5 rounded-lg border border-border bg-secondary/50 px-2.5 py-2 text-[11px] text-muted-foreground">
                <Icon name="Info" className="mt-0.5 h-3 w-3 shrink-0" />
                <span>
                  {onlyBoard
                    ? count === 1
                      ? "It is on a team board. It leaves your lists, and the board keeps it."
                      : "They are on team boards. They leave your lists, and the boards keep them."
                    : `${boardCount} of these ${boardCount === 1 ? "is" : "are"} on a team board. Those leave your lists, and the board keeps them.`}
                </span>
              </div>
            )}
          </div>
          <button
            onClick={cancelPendingDelete}
            aria-label="Cancel"
            className="text-muted-foreground hover:text-foreground"
          >
            <Icon name="X" className="h-4 w-4" />
          </button>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="text" size="none" layout="" onClick={cancelPendingDelete} className="px-3 py-1.5 text-xs">
            Cancel
          </Button>
          <button
            onClick={confirmPendingDelete}
            autoFocus
            className="inline-flex items-center gap-1.5 rounded-lg bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground hover:opacity-90"
          >
            <Icon name={onlyBoard ? "UserX" : "Trash2"} className="h-3.5 w-3.5" />
            {verb}
          </button>
        </div>
      </div>
    </div>
  );
}
