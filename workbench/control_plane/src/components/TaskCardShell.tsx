"use client";

/**
 * The task card's box — one shell for /projects and /tasks (WS-27ad).
 *
 * WS-27s made the two apps agree about the chip ROW inside a card
 * (`lib/taskCard.ts` + `TaskMeta`). The box around it still disagreed:
 * /tasks drew `rounded-lg border bg-card p-3 shadow-sm` and /projects
 * `rounded-md border bg-background p-2`, with different title sizes, different
 * hover and different selected states. Two objects, one concept.
 *
 * /tasks' box wins, and the reason is not seniority: `bg-card` is the token
 * that means "a raised surface" (`bg-background` is the page under it, so a
 * /projects card was a card-shaped hole rather than a card), and the shadow
 * lift is what makes a draggable object read as pick-up-able before anybody
 * drags it. **That lift is the only drag affordance either board has** —
 * neither draws a grip, because the whole card is `draggable` and a grip
 * points at a spot that is not special (S1).
 *
 * S1 also removed the last claim this comment used to make about the surface
 * underneath: /tasks' columns were `bg-secondary/30` and /projects' `bg-card`,
 * and both are `bg-card` now. A card is told apart from its column by border
 * and shadow, not by a second fill.
 *
 * WHAT goes inside stays each app's business — /projects feeds it
 * `lib/card.ts` facts and honours its own `shown_fields`, /tasks feeds it My Tasks
 * badges. Only the shell and its visual grammar are shared.
 */

import type React from "react";

export function TaskCardShell({
  children,
  selected = false,
  atCursor = false,
  draggable = false,
  completed = false,
  className = "",
  innerRef,
  onActivate,
  onContextMenu,
  onDragStart,
  onDragEnd,
  ariaLabel,
}: {
  children: React.ReactNode;
  /** Multi-selected — a primary border plus a ring, so it reads at a glance. */
  selected?: boolean;
  /** The keyboard cursor stands here (`lib/cursor.ts`). */
  atCursor?: boolean;
  draggable?: boolean;
  /** Drawn quieter: a finished task is context, not work. */
  completed?: boolean;
  className?: string;
  innerRef?: (element: HTMLDivElement | null) => void;
  /**
   * Click / Enter / Space. `shift` is passed through rather than swallowed
   * because in a selection mode the card IS the checkbox, and a shift-click on
   * it has to mean the same range-extend it means on a real one
   * (`@/lib/selection`).
   */
  onActivate?: (shift: boolean) => void;
  onContextMenu?: (event: React.MouseEvent) => void;
  onDragStart?: (event: React.DragEvent) => void;
  onDragEnd?: (event: React.DragEvent) => void;
  ariaLabel?: string;
}) {
  return (
    // A div with role=button rather than a real <button>: both apps put their
    // own buttons and checkboxes inside a card, and nested interactive elements
    // in a <button> are invalid HTML that browsers resolve by swallowing the
    // inner click. Enter/Space are wired below so it stays keyboard-usable.
    <div
      ref={innerRef}
      role="button"
      tabIndex={0}
      aria-label={ariaLabel}
      draggable={draggable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={(event) => onActivate?.(event.shiftKey)}
      onContextMenu={onContextMenu}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onActivate?.(event.shiftKey);
        }
      }}
      className={[
        "group tech-transition relative flex cursor-pointer flex-col gap-2 rounded-lg border bg-card p-3 text-left shadow-sm hover:shadow-md",
        selected
          ? "border-primary ring-1 ring-primary"
          : "border-border hover:border-primary/40",
        // The cursor ring is drawn OUTSIDE the selection ring deliberately: a
        // card can be both, and one overwriting the other is how "where am I"
        // and "what did I pick" become the same signal.
        atCursor ? "ring-2 ring-ring" : "",
        completed ? "opacity-70" : "",
        className,
      ].join(" ")}
    >
      {children}
    </div>
  );
}

/**
 * A caller-supplied class that fights the shell's own clamp.
 *
 * Stripped rather than merged, and that is the whole design decision: `truncate`
 * sets `white-space: nowrap`, `line-clamp-2` sets `display: -webkit-box`, and
 * when both land on one element which of them wins is CSS source order — i.e.
 * invisible in review and not necessarily the same in dev and in a production
 * build. So the clamp belongs to exactly one place, this file.
 */
const CLAMP_OVERRIDE = /\b(?:truncate|line-clamp-\d+|whitespace-nowrap)\b/g;

/**
 * The card's title line.
 *
 * Shared because the size and weight are the loudest single difference between
 * the two cards: /projects drew a plain `text-sm`, /tasks a `text-[13px]
 * font-medium leading-snug`, and side by side that alone made them look like
 * different products.
 *
 * **How many lines it gets is decided here, for both apps (S1).** They
 * disagreed: /projects clamped a board title to one line, /tasks let it wrap
 * without limit — and neither is right for the other's data. A `pm_tasks` title
 * is usually a noun phrase, so one line loses little; a GTD next action is
 * often a whole sentence ("email Priya the revised quote before the review"),
 * and one line cuts it exactly where the verb's object would have been. Two
 * lines is the compromise, taken in the shared file so the two boards cannot
 * drift apart again (AGENTS.md rule 4).
 *
 * What it costs: a /tasks title longer than two lines now ends in an ellipsis
 * where it used to wrap in full, and a /projects card is up to one line taller.
 * The full text is a click away in either app's detail view. A surface that
 * genuinely needs a different clamp should grow a prop here rather than pass a
 * class — see `CLAMP_OVERRIDE`.
 */
export function TaskCardTitle({
  children,
  completed = false,
  className = "",
}: {
  children: React.ReactNode;
  completed?: boolean;
  className?: string;
}) {
  return (
    <p
      className={[
        "min-w-0 text-[13px] font-medium leading-snug text-foreground line-clamp-2",
        completed ? "line-through opacity-60" : "",
        className.replace(CLAMP_OVERRIDE, ""),
      ].join(" ")}
    >
      {children}
    </p>
  );
}
