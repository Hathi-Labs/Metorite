"use client";

/**
 * The two subtask questions, drawn once for Projects and My Tasks
 * (D-PM-38 decisions 2 and 4, Subtasks S5).
 *
 * - `CompleteSubtasksDialog` — "3 subtasks are still open. Complete them
 *   too?" It is the shared `ConfirmDialog` (Modal `layer="alert"`).
 *   [Only this task] is the owner's default, so it is the SOLID primary
 *   button, it has focus, and Escape and the close button mean it. It still
 *   completes the parent. [Complete all] is the secondary, outline button.
 * - `IncludeSubtasksBox` — the pre-ticked "Include N subtasks" box the move
 *   and archive dialogs and the bulk bar draw. It draws NOTHING when N is 0.
 *
 * The words come from `@/lib/subtaskCascade`, which both apps read.
 */

import { useCallback, useRef, useState } from "react";

import Checkbox from "@/components/ui/Checkbox";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { completePrompt, includeSubtasksLabel } from "@/lib/subtaskCascade";

export function CompleteSubtasksDialog({
  open,
  subject,
  note,
  onAnswer,
}: {
  /** Open subtasks, from the chip. 0 closes the dialog. */
  open: number;
  /** The parent's title, quoted under the heading. */
  subject?: string | null;
  /** The bulk bar's line, for a batch: "includes 2 parents with 5 open subtasks". */
  note?: string | null;
  /** `true` for Complete all, `false` for Only this task (and Escape). */
  onAnswer: (includeSubtasks: boolean) => void;
}) {
  const words = completePrompt(open);
  return (
    <ConfirmDialog
      open={words !== null}
      title={words?.title ?? ""}
      subject={subject}
      note={note}
      body={words?.body ?? ""}
      confirmLabel={words?.confirmLabel ?? ""}
      cancelLabel={words?.cancelLabel}
      defaultFocus="cancel"
      emphasis="cancel"
      icon="ListChecks"
      onConfirm={() => onAnswer(true)}
      onCancel={() => onAnswer(false)}
    />
  );
}

/**
 * Ask the complete question, and answer with the member's choice.
 *
 * `ask(n, subject)` resolves at once with `false` when `n` is 0, so a caller
 * never draws a prompt for a task with no open subtasks. Otherwise it opens
 * the dialog and resolves when the member picks. `dialog` is the element the
 * caller mounts once.
 */
export function useCompleteSubtasksPrompt() {
  const [pending, setPending] = useState<{ open: number; subject?: string | null } | null>(
    null,
  );
  const resolver = useRef<((include: boolean) => void) | null>(null);

  const ask = useCallback((open: number, subject?: string | null): Promise<boolean> => {
    if (open <= 0) return Promise.resolve(false);
    // A second question replaces an unanswered first one, which reads as
    // "Only this task": the member moved on without saying yes.
    resolver.current?.(false);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
      setPending({ open, subject });
    });
  }, []);

  const dialog = (
    <CompleteSubtasksDialog
      open={pending?.open ?? 0}
      subject={pending?.subject}
      onAnswer={(include) => {
        const done = resolver.current;
        resolver.current = null;
        setPending(null);
        done?.(include);
      }}
    />
  );
  return { ask, dialog };
}

export function IncludeSubtasksBox({
  count,
  checked,
  onChange,
  disabled,
  hidden = 0,
  refused = [],
}: {
  /** Subtasks the act would take along. The box draws nothing at 0. */
  count: number;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  /** Subtasks the member cannot see. A move with them is refused (409). */
  hidden?: number;
  /**
   * Why Move is held for a carried subtask (D62), in the gateway's words.
   * Drawn only while the box is ticked, because unticking is the remedy.
   */
  refused?: readonly string[];
}) {
  if (count <= 0 && hidden <= 0) return null;
  return (
    <div className="space-y-1" data-include-subtasks>
      <label className="flex items-center gap-2 text-xs text-foreground">
        <Checkbox
          size="sm"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.currentTarget.checked)}
        />
        <span>{includeSubtasksLabel(count + hidden)}</span>
      </label>
      {checked && hidden > 0 ? (
        <p className="text-[11px] text-destructive">
          {hidden === 1 ? "1 subtask is" : `${hidden} subtasks are`} hidden from you, so
          the move is refused with the box ticked. Untick it to move without the
          subtasks.
        </p>
      ) : null}
      {checked
        ? refused.map((reason) => (
            <p key={reason} role="alert" className="text-[11px] text-destructive">
              {reason}
            </p>
          ))
        : null}
    </div>
  );
}
