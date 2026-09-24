"use client";

import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { useTaskStore } from "../lib/taskStore";
import { removalCopy } from "../lib/removal";
import { useRemoval } from "../lib/useRemoval";

/**
 * My Tasks' delete confirmation: the shared `ConfirmDialog`, with My Tasks'
 * own words.
 *
 * Store-driven: any delete site calls `requestDelete(ids)`, which opens this
 * for an already-clarified task (a fresh inbox capture skips it, and Undo is
 * enough there). Confirming runs `deleteItems`.
 *
 * The words come from `removalCopy`, which states what really happens (S6g):
 * my own task can be undone for a few seconds and is then deleted for good.
 * A board task only leaves my lists, and the board keeps it.
 */
export function DeleteConfirmModal() {
  const pendingDeleteIds = useTaskStore((s) => s.pendingDeleteIds);
  const items = useTaskStore((s) => s.items);
  const confirmPendingDelete = useTaskStore((s) => s.confirmPendingDelete);
  const cancelPendingDelete = useTaskStore((s) => s.cancelPendingDelete);
  // S6g, P0 — a board task is removed from my lists, never deleted.
  const { scope } = useRemoval();

  const targets = (pendingDeleteIds ?? [])
    .map((id) => items.find((i) => i.id === id))
    .filter((t): t is NonNullable<typeof t> => !!t);
  const copy = removalCopy(targets, scope);

  return (
    <ConfirmDialog
      open={targets.length > 0}
      title={copy.title}
      subject={targets.length === 1 ? targets[0].title : null}
      body={copy.body}
      note={copy.note}
      confirmLabel={copy.confirmLabel}
      icon={copy.icon}
      onConfirm={confirmPendingDelete}
      onCancel={cancelPendingDelete}
    />
  );
}
