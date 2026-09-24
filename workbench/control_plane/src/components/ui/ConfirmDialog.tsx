"use client";

/**
 * The one "are you sure?" dialog, for Projects and My Tasks.
 *
 * Before it, Projects asked with `window.confirm` (the browser's own box, in
 * the browser's font, with no theme) and My Tasks drew a hand-rolled
 * full-screen overlay with no focus trap. One act, two looks, and two sets
 * of words. This is built on `Modal`, so it has the scrim, the focus trap,
 * Escape and focus return that `DESIGN_SYSTEM.md` §4a names.
 *
 * **The caller owns the words, and they must be true.** This component says
 * nothing about what a delete does. Each app computes its copy in a pure
 * function with a test (`app/tasks/lib/removal.ts` `removalCopy`,
 * `app/projects/lib/deleteCopy.ts`), because only the app knows whether its
 * delete can be undone.
 *
 * Focus opens on the confirm button, so Enter confirms and Escape cancels.
 * That keeps the keys My Tasks' dialog had.
 *
 * Fence: `ConfirmDialog.test.ts`.
 */

import { useRef } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** The task's title, quoted under the heading when there is one. */
  subject?: string | null;
  /** What the act does, as it really happens. */
  body: string;
  /** A second fact, drawn in a quiet box. */
  note?: string | null;
  confirmLabel: string;
  /** A Lucide name for the heading and the confirm button. */
  icon?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  subject,
  body,
  note,
  confirmLabel,
  icon = "Trash2",
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const footer = useRef<HTMLDivElement>(null);
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      icon={icon}
      size="sm"
      closeLabel="Cancel"
      initialFocus={() =>
        footer.current?.querySelector<HTMLElement>("[data-confirm]") ?? true
      }
    >
      <div className="space-y-2 px-3 py-3">
        {subject ? (
          <p className="truncate text-xs text-muted-foreground">
            &ldquo;{subject}&rdquo;
          </p>
        ) : null}
        <p className="whitespace-pre-line text-xs text-foreground">{body}</p>
        {note ? (
          <p className="flex items-start gap-1.5 rounded-md border border-border bg-secondary/50 px-2.5 py-2 text-[11px] text-muted-foreground">
            <Icon name="Info" className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{note}</span>
          </p>
        ) : null}
      </div>
      <div
        ref={footer}
        className="flex items-center justify-end gap-2 border-t border-border px-3 py-2"
      >
        <Button variant="secondary" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          size="sm"
          icon={icon}
          loading={busy}
          onClick={onConfirm}
          data-confirm=""
        >
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
