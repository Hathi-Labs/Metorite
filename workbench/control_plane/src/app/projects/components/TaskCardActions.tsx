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
 *    ⚠️ Focusable was not enough, and the first version of this file was
 *    wrong about it. `TaskCardShell` calls `preventDefault()` on Enter and
 *    Space from ANY descendant, which CANCELS the click the browser would
 *    have synthesised — so Enter on "Mark done" opened the task panel and
 *    the action never ran. `SWALLOW` stops the keyboard as well as the
 *    pointer for that reason. `CardInput.tsx` defends itself against the
 *    same shell behaviour, and this file did not.
 * 3. **⚠️ TAILWIND'S `hover:` IS MEDIA-GATED, AND THAT SHIPPED A DEFECT.**
 *    Read this before touching the reveal.
 *
 *    Tailwind v4 compiles every `hover:` and `group-hover:` variant inside
 *    `@media (hover: hover)`. Chromium reports `hover: none` for ANY
 *    touch-capable display — a Windows laptop with a touchscreen included,
 *    even when the member is driving it with a mouse. On such a machine
 *    `group-hover/card:opacity-100` NEVER applies, so the strip would be
 *    invisible for ever.
 *
 *    The first version papered over that with
 *    `[@media(hover:none)]:opacity-100`, which pinned the strip AND the
 *    card's checkbox permanently on for exactly those members. The owner
 *    reported it on 2026-09-20 as "it's always on".
 *
 *    ⚠️ **So removing the pin is not the fix — it trades always-on for
 *    never-on.** The reveal uses an ARBITRARY variant instead
 *    (`[[data-card]:hover_&]`), which Tailwind emits verbatim with no media
 *    wrapper, so it follows the real `:hover` state whatever the display
 *    reports. Measured: with `hasTouch: true` the row matched `:hover` while
 *    `group-hover` still resolved to `opacity: 0`.
 *
 *    `data-card` rather than a named group because a Tailwind group name
 *    carries a `/`, which has to be escaped inside an arbitrary variant and
 *    is one silent-failure mode too many for something this load-bearing.
 *
 *    What it costs: on a display with NO pointer at all the strip is reached
 *    by keyboard focus or by the long-press menu, not by hovering. The board
 *    is not the phone surface — the app draws the table at that width — so
 *    this is a tablet-sized edge, and it is named rather than hidden.
 *
 * ⚠️ **The card is `draggable`, and these are inside it.** Every button stops
 * its own click, its own pointer-down and its own drag start — otherwise
 * pressing Rename begins dragging the card, and releasing drops it in a lane.
 *
 * ⚠️ **No motion.** Same directive `SelectButton.tsx` carries: the strip
 * appears as a state change, with no duration and no easing.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

import type {
  TaskMenuActions,
  TaskMenuContext,
  TaskQuickAction,
} from "../lib/taskMenu";

/** Kept out of the JSX so the hidden/shown pair is read as one decision. */
const REVEAL =
  "opacity-0 pointer-events-none " +
  // ⚠️ NOT `group-hover/card:` — see trap 3. This is the same selector
  // without Tailwind's `@media (hover: hover)` wrapper around it.
  "[[data-card]:hover_&]:opacity-100 [[data-card]:hover_&]:pointer-events-auto " +
  // The keyboard's door in, and the fallback on a display that never hovers.
  // ⚠️ `:focus-visible`, not plain `focus-within`: a MOUSE click leaves
  // focus on the button it hit, so with `focus-within` the strip stayed lit
  // on that card after the pointer had gone — a small version of the
  // always-on complaint. `:has(:focus-visible)` reveals it for the keyboard
  // and not for the mouse, which is the same distinction the house focus
  // ring already draws.
  "[&:has(:focus-visible)]:opacity-100 [&:has(:focus-visible)]:pointer-events-auto";

/**
 * ⚠️ The house `Button`, not a hand-rolled one.
 *
 * The first version of this file wrote its own ghost icon button as a class
 * string — `Button variant="ghost" size="icon-sm"` already exists and is the
 * same control, so that was a second way to do an existing thing (§5). It
 * also skipped `cc-button`, which is what gives the theme's radius, state
 * layer and focus ring.
 *
 * The glyph goes in as CHILDREN rather than through `icon`, because the tick
 * needs a heavier stroke when it is on and `Button` draws its own icon at a
 * fixed weight.
 */
const GHOST = { variant: "ghost", size: "icon-sm" } as const;

/**
 * Swallow every gesture the card underneath would otherwise claim.
 *
 * `onPointerDown` matters as much as `onClick`: the drag starts on the press,
 * so stopping only the click still lets a press-and-move on Rename drag the
 * whole card into another lane.
 *
 * ⚠️ **`onKeyDown` is the one that was missing, and without it the strip
 * was mouse-only.** The shell's Enter/Space handler runs on the bubble, calls
 * `preventDefault()`, and the button's synthesised click never happens — so
 * the keyboard opened the task instead of running the action, and the board's
 * cursor stepper then opened a SECOND task. Stopping the key here leaves the
 * button's own default activation untouched, because this does not
 * `preventDefault`.
 */
const SWALLOW = {
  draggable: false,
  onPointerDown: (event: React.PointerEvent) => event.stopPropagation(),
  onKeyDown: (event: React.KeyboardEvent) => event.stopPropagation(),
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
      {items
        .filter((item) => !item.trailing)
        .map((item) => (
          <QuickButton
            key={item.id}
            item={item}
            onRun={() => item.run(actions, ctx)}
          />
        ))}

      <Button
        {...GHOST}
        {...SWALLOW}
        type="button"
        title="More actions"
        aria-label="More actions"
        aria-haspopup="menu"
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
      </Button>

      {/* The mode, past a rule. See `TaskMenuAction.trailing`. */}
      {items
        .filter((item) => item.trailing)
        .map((item) => (
          <span key={item.id} className="flex items-center">
            <span className="mx-0.5 h-4 w-px bg-border" aria-hidden />
            <QuickButton item={item} onRun={() => item.run(actions, ctx)} />
          </span>
        ))}
    </div>
  );
}

/** One glyph button. Extracted so the row and the trailing slot agree. */
function QuickButton({
  item,
  onRun,
}: {
  item: TaskQuickAction;
  onRun: () => void;
}) {
  return (
    <Button
      {...GHOST}
      {...SWALLOW}
      type="button"
      // `title` as well as `aria-label`: these are glyphs with no words, and
      // "what does the tick do" is a question a tooltip answers and a
      // screen-reader label does not.
      title={item.label}
      aria-label={item.label}
      aria-pressed={item.active ? true : undefined}
      // ⚠️ The tone is DECLARED, never guessed here. `Checkbox.tsx`'s rule:
      // the accent means selection, a lane colour is a fact about the work.
      // See `TaskMenuAction.activeTone`.
      className={
        item.active
          ? item.tone === "success"
            ? "text-success"
            : "text-primary"
          : ""
      }
      onClick={(event) => {
        event.stopPropagation();
        event.preventDefault();
        onRun();
      }}
    >
      <Icon
        name={item.icon}
        size={14}
        // The toggle reads as ON at a glance rather than by its colour alone
        // — colour is the cue some people do not get.
        strokeWidth={item.active ? 3 : 2}
      />
    </Button>
  );
}

export default TaskCardActions;
