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
 */

import { useEffect, useRef } from "react";

export function CardInput({
  value,
  onChange,
  onCommit,
  onCancel,
  busy = false,
  placeholder,
  "aria-label": ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
  /** A write is in flight. The field stays readable and refuses a second one. */
  busy?: boolean;
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
    <input
      ref={field}
      type="text"
      value={value}
      disabled={busy}
      placeholder={placeholder}
      aria-label={ariaLabel}
      // The house field paint, at the card's own text size. Not the `Input`
      // primitive: this one sits inside a card and has to read as the title it
      // replaced, so it carries no label slot, no icon slot and no size ramp.
      className="w-full rounded border border-primary bg-background px-1.5 py-1 text-[13px] font-medium leading-snug text-foreground outline-none disabled:opacity-60"
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
  );
}

export default CardInput;
