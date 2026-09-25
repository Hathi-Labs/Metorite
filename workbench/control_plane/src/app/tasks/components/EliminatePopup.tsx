"use client";

import Modal from "@/components/ui/Modal";
import Icon from "@/components/Icon";
import { useTaskStore } from "../lib/taskStore";
import { useRemoval } from "../lib/useRemoval";

// The "Eliminate" popup — opened from the Eliminate pill or a context menu (via
// store.openEliminate(id)). The matrix reads the task as better dropped than
// done; this offers the two honest ways to let it go: park it in Someday/Maybe
// (reversible, no guilt) or delete it (which routes through the EXISTING
// confirm — requestDelete → DeleteConfirmModal). Global, mounted in page.tsx.
export function EliminatePopup() {
  const eliminateItemId = useTaskStore((s) => s.eliminateItemId);
  const items = useTaskStore((s) => s.items);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const requestDelete = useTaskStore((s) => s.requestDelete);
  const closeEliminate = useTaskStore((s) => s.closeEliminate);
  const removal = useRemoval();

  const item = eliminateItemId
    ? items.find((i) => i.id === eliminateItemId)
    : undefined;
  if (!item) return null;

  // The shared `Modal` (continuity P3). The `alert` layer, because the
  // Eliminate nudge also opens this from inside the focused task
  // (`TaskFocusModal`, z-[80]), and the dialog layer (z-50) would paint under
  // it. The delete arm closes this first, then raises the ConfirmDialog.
  return (
    <Modal
      open
      onClose={closeEliminate}
      title="Let this go?"
      description={<span className="block truncate">{item.title}</span>}
      icon="Archive"
      size="sm"
      layer="alert"
    >

        <div className="flex flex-col gap-2 px-4 py-3">
          <button
            type="button"
            onClick={() => {
              quickDispose(item.id, "SOMEDAY");
              closeEliminate();
            }}
            className="tech-transition flex items-start gap-2.5 rounded-lg border border-border bg-background/60 p-3 text-left hover:border-primary/50 active:bg-primary/5"
          >
            <Icon name="MoonStar" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span className="min-w-0">
              <span className="block text-sm font-medium text-foreground">
                Move to Someday / Maybe
              </span>
              <span className="block text-[11px] text-muted-foreground">
                Park it out of your next actions — you can pull it back anytime.
              </span>
            </span>
          </button>

          <button
            type="button"
            onClick={() => {
              // Route through the existing confirm flow (DeleteConfirmModal).
              closeEliminate();
              requestDelete([item.id]);
            }}
            className="tech-transition flex items-start gap-2.5 rounded-lg border border-border bg-background/60 p-3 text-left hover:border-destructive/50 hover:bg-destructive/5"
          >
            <Icon name="Trash2" className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <span className="min-w-0">
              <span className="block text-sm font-medium text-foreground">
                {removal.label(item)}
              </span>
              <span className="block text-[11px] text-muted-foreground">
                {removal.canPurge(item)
                  ? "Remove it for good — with a confirmation."
                  : "It leaves your lists. The board keeps it for the team."}
              </span>
            </span>
          </button>
        </div>
    </Modal>
  );
}
