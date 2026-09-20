"use client";

/**
 * Projects · the strip of buttons a board card shows under the pointer.
 *
 * Owner direction, 2026-09-20: *"When I hover on top of the card, some
 * additional options should also show up … marking as done, adding a subtask,
 * renaming the task, three dots to show more context options."*
 *
 * ## What it draws, and what decides that
 *
 * **Nothing here decides which buttons exist.** The list arrives from
 * `lib/taskMenu.taskQuickActions`, which reads the same declared registry the
 * right-click menu reads. This file owns the paint and the gestures. The day
 * a fourth button is wanted, it is one entry in `taskMenu.ts` and no change
 * here — which is the whole point of the registry.
 *
 * The one control that is NOT from the registry is the trailing `⋯`. It opens
 * the right-click menu at the button, so it is not an action, it is a second
 * way to reach the menu for people who do not right-click. Phones have no
 * right-click at all, which is why it is not optional.
 *
 * ## Three traps, all of which ship green
 *
 * 1. **`opacity-0` alone leaves an invisible button clickable.** A hidden
 *    strip over the top-right corner of every card swallows the click that was
 *    meant to open the task. So the hidden state is `pointer-events-none` too,
 *    and hover/focus restore both together.
 * 2. **`hidden` or `invisible` would take it out of the tab order.** Then the
 *    only way to finish a task is a mouse. `opacity-0` keeps the buttons
 *    focusable, and `focus-within` paints them the moment one has focus.
 * 3. **A touch screen never hovers.** `[@media(hover:none)]` pins the strip
 *    on, which is the same rule `app/chat/page.tsx` applies to its own
 *    hover-revealed control. Without it the whole feature is desktop-only and
 *    nothing says so.
 *
 * ⚠️ **The card is `draggable`, and these are inside it.** Every button stops
 * its own click, its own pointer-down and its own drag start — otherwise
 * pressing Rename begins dragging the card, and releasing drops it in a lane.
 *
 * ⚠️ **No motion.** Same directive `SelectButton.tsx` carries: the strip
 * appears as a state change, with no duration and no easing.
 */

import Icon from "@/components/Icon";

import type {
  TaskMenuActions,
  TaskMenuContext,
  TaskQuickAction,
} from "../lib/taskMenu";

/** Kept out of the JSX so the hidden/shown pair is read as one decision. */
const REVEAL =
  "opacity-0 pointer-events-none " +
  "group-hover/card:opacity-100 group-hover/card:pointer-events-auto " +
  "focus-within:opacity-100 focus-within:pointer-events-auto " +
  "[@media(hover:none)]:opacity-100 [@media(hover:none)]:pointer-events-auto";

const BUTTON =
  "flex h-6 w-6 items-center justify-center rounded " +
  "text-muted-foreground hover:bg-muted hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/**
 * Swallow every gesture the card underneath would otherwise claim.
 *
 * `onPointerDown` matters as much as `onClick`: the drag starts on the press,
 * so stopping only the click still lets a press-and-move on Rename drag the
 * whole card into another lane.
 */
const SWALLOW = {
  draggable: false,
  onPointerDown: (event: React.PointerEvent) => event.stopPropagation(),
  onDragStart: (event: React.DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
  },
};

export function TaskCardActions({
  items,
  actions,
  ctx,
  onMore,
}: {
  items: readonly TaskQuickAction[];
  actions: TaskMenuActions;
  ctx: TaskMenuContext;
  /** Open the right-click menu at this point. */
  onMore: (at: { x: number; y: number }) => void;
}) {
  return (
    <div
      // ⚠️ **BOTTOM right, over the card's footer — not the top right.**
      // The strip is about 100px wide and it has to cover something. Over the
      // TITLE it costs the card's name: measured at 1440 and again at compact
      // density, "Notification engine for projects" became "Notification
      // engine for…" the moment the pointer arrived, and the fix for that
      // (a permanent inset) truncated titles that used to fit even when
      // nobody was hovering. Over the FOOTER it covers the reference number
      // and the avatars, which the reader has already taken in and which are
      // not what identifies the card.
      //
      // It also costs nothing in layout: the footer row is the same height on
      // every card and always present, so the strip needs no reserved space,
      // no held title height, and nothing reflows.
      //
      // `bg-card` with a border and a shadow, because it sits ON the card. A
      // transparent strip would read as part of the row underneath it.
      className={`absolute bottom-1.5 right-1.5 z-10 flex items-center gap-px rounded-md border border-border bg-card p-0.5 shadow-sm ${REVEAL}`}
    >
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          {...SWALLOW}
          // `title` as well as `aria-label`: these are glyphs with no words,
          // and "what does the tick do" is a question a tooltip answers and a
          // screen-reader label does not.
          title={item.label}
          aria-label={item.label}
          aria-pressed={item.active ? true : undefined}
          // ⚠️ `--success`, NOT `--primary`. `Checkbox.tsx`'s header states
          // the rule and the first draft here broke it: the accent means
          // SELECTION, and "this task is finished" is a fact about the work,
          // which is what `statusAccent.ts` spends `--success` on. Painted in
          // the accent, the tick changed colour when a member changed theirs
          // — the exact defect `underAccents` exists to catch.
          className={`${BUTTON} ${item.active ? "text-success" : ""}`}
          onClick={(event) => {
            event.stopPropagation();
            event.preventDefault();
            item.run(actions, ctx);
          }}
        >
          <Icon
            name={item.icon}
            size={14}
            // The tick reads as ON at a glance rather than by its colour
            // alone — colour is the cue some people do not get.
            strokeWidth={item.active ? 3 : 2}
          />
        </button>
      ))}

      <button
        type="button"
        {...SWALLOW}
        title="More actions"
        aria-label="More actions"
        aria-haspopup="menu"
        className={BUTTON}
        onClick={(event) => {
          event.stopPropagation();
          event.preventDefault();
          // Anchored to the BUTTON, not to the pointer. A keyboard activation
          // has no pointer, and `clientX` is then 0 — the menu would open in
          // the top-left corner of the window.
          const box = event.currentTarget.getBoundingClientRect();
          onMore({ x: box.left, y: box.bottom + 2 });
        }}
      >
        <Icon name="Ellipsis" size={14} />
      </button>
    </div>
  );
}

export default TaskCardActions;
