"use client";

/**
 * The inline group-context add (WS-27y, shared by /projects and /tasks).
 *
 * One control, many surfaces: a board column (or lane cell), a list group,
 * a calendar day — in either task app. Collapsed it is a quiet "+ Add"; open it is a title box
 * whose Enter submits AND stays open ready for the next title — batch entry
 * is the whole point, a capture box that closes after one item is a capture
 * box used once. Esc closes it and throws the draft away.
 *
 * WHAT the created task inherits from its group is not this component's
 * business: the caller owns the axis→payload mapping (`lib/quickAdd.ts`) and
 * this form only collects the title. That split is what the spreadsheet
 * layout (WS-27x) reuses.
 */

import Icon from "@/components/Icon";
import { Input } from "@/components/ui/Input";
import { useState } from "react";

interface Props {
  /** Where the task will land, e.g. `Add to “In progress”`. Doubles as the
   *  input's placeholder and accessible name. */
  label: string;
  /** Create the task. Throwing keeps the draft and shows the message. */
  onAdd: (title: string) => Promise<void>;
  className?: string;
  /** Compact trigger for tight cells (calendar days). */
  compact?: boolean;
  /** Open at once, as the board's header "+" asks (`BoardColumn`). */
  startOpen?: boolean;
  /** Esc closed the box. A box that opened at once leaves with it. */
  onClose?: () => void;
}

export function QuickAdd({ label, onAdd, className = "", compact = false, startOpen = false, onClose }: Props) {
  const [open, setOpen] = useState(startOpen);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={label}
        aria-label={label}
        className={`flex w-full items-center gap-1 rounded-md px-1.5 py-1 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground ${className}`}
      >
        <Icon name="Plus" size={12} />
        {compact ? null : <span>Add</span>}
      </button>
    );
  }

  return (
    <form
      className={className}
      // Keystrokes typed here belong to this box, not to the surface's
      // keyboard cursor behind it.
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          setOpen(false);
          setTitle("");
          setError(null);
          onClose?.();
        }
        e.stopPropagation();
      }}
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = title.trim();
        if (!trimmed || busy) return;
        setBusy(true);
        setError(null);
        onAdd(trimmed)
          .then(() => setTitle(""))
          .catch((err) => setError(String((err as Error).message)))
          .finally(() => setBusy(false));
      }}
    >
      <Input
        autoFocus
        inputSize="sm"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder={label}
        aria-label={label}
        // Not `disabled` while busy — disabling blurs the input, and the
        // next title should be typable the instant this one lands.
        readOnly={busy}
      />
      {error ? <p className="mt-1 text-xs text-destructive">{error}</p> : null}
    </form>
  );
}
