"use client";

/**
 * The task panel's header, shared by Projects' `TaskPanel` and My Tasks'
 * `ItemDetail` (2026-09-24).
 *
 * The two headers carried the same controls in two orders, under two names,
 * with two sizes of close button. Projects said "Close task" at `icon-sm`, My
 * Tasks said "Close detail" at `icon-xs`. Projects' expand said "Open as a
 * full card", My Tasks' said "Open full page" and was a raw `<button>`. And
 * the title was editable in one app and not in the other.
 *
 * One order now, in both apps:
 *
 *   #ref [copy link] · app chips … · app actions · [expand] [close]
 *   Title (click to edit)
 *
 * - `taskRef` — the task's reference ("#42"), or "Task" when it has none.
 * - `linkFor` — the URL the copy button puts on the clipboard. Omit it and
 *   the button is absent.
 * - `chips` — the app's own chips (a status, a disposition picker, the
 *   source, Focus).
 * - `actions` — the app's own icon buttons (Archive, Delete).
 * - `expand` — the side panel, or the same panel as a full card.
 * - `onClose` — omit it where the surface draws its own close.
 *
 * Every icon button is a `Button`. Fence: `TaskPanelHeader.test.ts`.
 */

import { type ReactNode, useState } from "react";

import AppIcon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { CLOSE_TASK_LABEL, EXPAND_ICONS, EXPAND_LABELS } from "@/lib/taskPanel";

export interface TaskHeaderExpand {
  /** The panel is the full card now. */
  expanded: boolean;
  onToggle: () => void;
}

export function TaskHeaderRow({
  taskRef,
  linkFor,
  chips,
  actions,
  expand,
  onClose,
}: {
  taskRef: string;
  linkFor?: () => string;
  chips?: ReactNode;
  actions?: ReactNode;
  expand?: TaskHeaderExpand;
  onClose?: () => void;
}) {
  // The copy's "it worked" flash. A copy with no acknowledgement gets
  // clicked three times.
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (!linkFor) return;
    try {
      await navigator.clipboard.writeText(linkFor());
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // The clipboard can be unavailable (permissions, an insecure context).
      // The link is not lost: the deep link shows in the address bar.
    }
  }

  const stop = expand?.expanded ? "full" : "side";

  return (
    <div className="mb-1.5 flex items-start justify-between gap-2">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <div className="flex min-w-0 items-center gap-0.5">
          <p className="truncate text-xs text-muted-foreground">{taskRef}</p>
          {linkFor ? (
            <Button
              variant="ghost"
              size="icon-xs"
              icon={copied ? "Check" : "Link"}
              aria-label="Copy a link to this task"
              title="Copy a link that opens this task"
              onClick={() => void copy()}
            />
          ) : null}
        </div>
        {chips}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {actions}
        {/* ⚠️ NO `aria-pressed`. The label names the ACTION, so a pressed
            state would announce one fact twice. */}
        {expand ? (
          <Button
            variant="ghost"
            size="icon-xs"
            icon={EXPAND_ICONS[stop]}
            aria-label={EXPAND_LABELS[stop]}
            title={EXPAND_LABELS[stop]}
            onClick={expand.onToggle}
          />
        ) : null}
        {onClose ? (
          <Button
            variant="ghost"
            size="icon-sm"
            icon="X"
            aria-label={CLOSE_TASK_LABEL}
            title={CLOSE_TASK_LABEL}
            onClick={onClose}
          />
        ) : null}
      </div>
    </div>
  );
}

/**
 * The task title, edited in place. Click it, type, then Enter or click away
 * to save. Escape puts it back. Shift+Enter is a new line.
 *
 * An h2: the app bar holds the page's one h1. The button sits INSIDE
 * the heading, because a heading is not allowed inside a button.
 *
 * `onSave` gets the trimmed text, and only when it is new and not empty.
 */
export function EditableTaskTitle({
  value,
  onSave,
}: {
  value: string;
  onSave: (next: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  if (!editing) {
    return (
      <h2 className="text-base font-semibold leading-snug text-foreground">
        <button
          type="button"
          onClick={() => {
            setDraft(value);
            setEditing(true);
          }}
          className="group flex w-full items-start gap-2 text-left"
          title="Click to edit"
        >
          <span className="min-w-0 flex-1">{value}</span>
          <AppIcon
            name="Pencil"
            className="mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-opacity reveal-on-hover"
          />
        </button>
      </h2>
    );
  }

  const save = () => {
    const next = titleToSave(draft, value);
    if (next !== null) onSave(next);
    setEditing(false);
  };

  return (
    <textarea
      autoFocus
      value={draft}
      rows={2}
      aria-label="Task title"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          save();
        }
        if (e.key === "Escape") {
          // The panel closes on Escape too. Stop here, so one key does one
          // thing: it undoes the edit and leaves the panel open.
          e.stopPropagation();
          setDraft(value);
          setEditing(false);
        }
      }}
      className="w-full resize-none rounded-md border border-primary/40 bg-background px-2 py-1 text-base font-semibold leading-snug text-foreground focus:outline-none"
    />
  );
}

/**
 * What a title edit saves: the trimmed text, or null when there is nothing to
 * save — an empty title, or the one already there. A pure function, so
 * `TaskPanelHeader.test.ts` can pin it without a DOM.
 */
export function titleToSave(draft: string, current: string): string | null {
  const next = draft.trim();
  if (!next || next === current) return null;
  return next;
}
