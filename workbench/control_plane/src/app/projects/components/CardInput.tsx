"use client";

/**
 * Projects · a one-line field opened INSIDE a task card.
 *
 * Two callers: Rename (it replaces the title) and Add subtask (it appears
 * under the chip row). One component, because the hard part is identical and
 * it is not the input — it is everything the card underneath would otherwise
 * do to the keystrokes.
 *
 * ## What it has to defend against, all of it measured in this tree
 *
 * 1. **`TaskCardShell` turns Enter and Space into "open the task".** Its
 *    `onKeyDown` calls `preventDefault()` then `onActivate`, and a keydown in
 *    a descendant bubbles straight into it. Un-defended, the space bar cannot
 *    be typed and Enter opens the panel over the field. So EVERY key stops
 *    here — not only Enter and Escape.
 * 2. **The board's own keyboard cursor.** `TaskBoard.onKeyDown` already bails
 *    when the event started inside an `input`, so it needs nothing from us.
 *    That is the one guard already in place; do not remove it on the strength
 *    of this file, because the list and table surfaces share it.
 * 3. **The card is a click target.** A click to put the caret back in the
 *    middle of a word would also open the task panel.
 * 4. **The card is `draggable`.** Selecting text with the mouse starts a card
 *    drag. The caller drops `draggable` while a field is open; the pointer
 *    guard here is the second half of that, for the press itself.
 *
 * ## Committing
 *
 * Enter commits, Escape abandons, and blur commits. Blur-commits rather than
 * blur-discards because the field holds something the member typed, and the
 * gesture that loses it must not be "clicked somewhere else". The caller's
 * commit is a no-op on an empty or unchanged value, so a blur that follows
 * Escape writes nothing.
 *
 * ## While a write is in flight, and when it fails
 *
 * ⚠️ **`readOnly` while busy, NEVER `disabled`.** This repo already wrote
 * the rule down — `components/QuickAdd.tsx`: "Not `disabled` while busy —
 * disabling blurs the input". The first version here used `disabled`, so a
 * failed rename dropped focus to `<body>` and the mount effect that focuses
 * the field has `[]` deps and never runs again. The member was left looking
 * at their own text with no caret and no message, which reads as nothing
 * having happened.
 *
 * A failure draws its reason under the field. The caller owns the text; this
 * only renders it, because only the caller knows which request failed.
 */

import { useEffect, useRef } from "react";

export function CardInput({
  value,
  onChange,
  onCommit,
  onCancel,
  busy = false,
  error = null,
  placeholder,
  "aria-label": ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
  /** A write is in flight. The field stays readable and refuses a second one. */
  busy?: boolean;
  /** Why the last commit failed. Drawn under the field; the caret stays. */
  error?: string | null;
  placeholder?: string;
  "aria-label": string;
}) {
  const field = useRef<HTMLInputElement | null>(null);
  /** Escape must beat the blur that follows it. */
  const abandoned = useRef(false);

  useEffect(() => {
    const node = field.current;
    if (!node) return;
    node.focus();
    // Selected, not just focused: Rename opens on the existing title, and the
    // common case is replacing it rather than appending to it.
    node.select();
  }, []);

  return (
    <div className="min-w-0">
    <input
      ref={field}
      type="text"
      value={value}
      // ⚠️ `readOnly`, not `disabled`. See the header.
      readOnly={busy}
      aria-busy={busy || undefined}
      aria-invalid={error ? true : undefined}
      placeholder={placeholder}
      aria-label={ariaLabel}
      // The house field paint, at the card's own text size. Not the `Input`
      // primitive: this one sits inside a card and has to read as the title it
      // replaced, so it carries no label slot, no icon slot and no size ramp.
      className={`w-full rounded border bg-background px-1.5 py-1 text-[13px] font-medium leading-snug text-foreground outline-none ${
        error ? "border-destructive" : "border-primary"
      } ${busy ? "opacity-70" : ""}`}
      onChange={(event) => onChange(event.target.value)}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onDragStart={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        // ⚠️ Every key, unconditionally. See trap 1 in the header.
        event.stopPropagation();
        if (event.key === "Enter") {
          event.preventDefault();
          if (!busy) onCommit();
        } else if (event.key === "Escape") {
          event.preventDefault();
          abandoned.current = true;
          onCancel();
        }
      }}
      onBlur={() => {
        if (abandoned.current) {
          abandoned.current = false;
          return;
        }
        if (!busy) onCommit();
      }}
    />
      {error ? (
        <p className="mt-1 text-[11px] text-destructive">{error}</p>
      ) : null}
    </div>
  );
}

export default CardInput;
