"use client";

/**
 * One row of a rail of named items: a tree node, a list, a folder, a view.
 *
 * Owner, 2026-10-10, with ClickUp's sidebar as the reference: *"You can see
 * how neat and clean all of the text is appearing, and we can easily read
 * this thing."* Before this, every Projects row kept a "···" and a "+" on
 * screen at all times. They took about 60 px from each name at a 256 px
 * rail, so "Company Operations" read as "Company Oper…".
 *
 * ## The shape
 *
 *     [indent + guides][icon  label ……………  meta][actions]
 *
 * - **indent** — one 16 px step per level, each with an optional thin
 *   vertical guide, so a nested level reads as nested. The guide sits 8 px
 *   in, under the centre of the parent's 16 px icon, which leaves 8 px to the
 *   child's icon. The step was 12 px until the owner found the icon "too
 *   close to the vertical line" (2026-10-10): 4 px read as untidy.
 * - **icon** — the row's marker. On a row that can open (`expand`), the
 *   expand chevron takes the SAME slot on hover and on keyboard focus, and
 *   the icon hides. ClickUp does this, and it removes the chevron column,
 *   which took 22 px from every name. The toggle is a real button at all
 *   times. At rest it is transparent, but Tab reaches it, and its label and
 *   `aria-expanded` are always there. The swap is for a device that can
 *   hover. On a phone the chevron has its own column and the icon stays, so
 *   a space keeps its glyph and a parent project keeps its run-state wheel.
 * - **label** — one line, cut with an ellipsis. `OverflowTip` shows the
 *   whole of it on hover and on keyboard focus.
 * - **the trailing zone** — `meta` (a muted count) at rest, and the row's
 *   `actions` on hover, on keyboard focus in the row, or while `pinned`. The
 *   actions take NO width at rest, which is the room the label gets back.
 *   `globals.css` (`rail-row-actions`, `rail-row-meta`) holds the swap.
 *
 * ## Touch
 *
 * The swap carries no media query (`reveal-on-hover`'s rule), so a
 * touch-capable laptop driven by a mouse behaves like any desktop. On a
 * phone (`useViewMode().isMobile`) nothing can hover, and choosing a row
 * closes the drawer the rail sits in. So each row draws its `phoneActions`
 * at all times, usually one "···" at a 40 px target, and every row grows to
 * 40 px for a finger. A rail that gives no `phoneActions` falls back to the
 * selected row keeping its `actions`.
 *
 * ## Keys
 *
 * On a row that can open, ArrowRight opens it and ArrowLeft closes it, from
 * the label or from the toggle. Enter and Space on the toggle switch it, as
 * on any button.
 *
 * ## What stays with the caller
 *
 * The row's behaviour: drag handlers, the context menu, the menus that the
 * actions open. They arrive through `rowProps` and `actions`. A row being
 * named or renamed passes an `editor`, which takes the label's place.
 *
 * Fences: `RailRow.test.ts` (markup), `src/lib/railRows.test.ts` (the rails
 * that must render this) and `e2e/rail-rows.spec.ts` (the real hover).
 */
import { useRef } from "react";

import { useViewMode } from "@/components/ViewModeProvider";
import Button from "@/components/ui/Button";
import { useOverflowTip } from "@/components/ui/OverflowTip";

/** One indent step, in px. The tree's own step before this primitive. */
export const RAIL_INDENT_PX = 12;

/** A level that groups (a space, a section head) or an item in it. */
export type RailRowTier = "group" | "item";

/** A row that opens and closes, such as a tree node with children. */
export interface RailRowExpand {
  expanded: boolean;
  onToggle: () => void;
}

/**
 * What a key does to a row that can open. ArrowRight opens a closed row and
 * ArrowLeft closes an open one. Any other key, or a key that would change
 * nothing, returns `false`, and the row leaves the key alone.
 */
export function expandKey(key: string, expanded: boolean): boolean {
  if (key === "ArrowRight") return !expanded;
  if (key === "ArrowLeft") return expanded;
  return false;
}

/** How long a recorded press stays the cause of a click, in ms. */
export const PRESS_FRESH_MS = 1000;

/**
 * What a press on the expand toggle does: switch the row, or select it.
 *
 * ⚠️ A TOUCH press on a row that is not selected SELECTS it. On a hover
 * device the chevron is transparent over the icon at rest, so the member
 * taps what looks like an icon. Chromium sets `:hover` at the tap's start,
 * and the tap's click then lands on a chevron that `rail-row-chevron` has
 * just switched on. The CSS alone cannot stop that, so this rule does. The
 * first tap selects the row and shows the chevron. A tap on the selected
 * row's chevron switches it.
 *
 * A mouse, a pen and a phone always switch. A phone draws its chevron in
 * its own column, so the member sees what they tap. A KEYBOARD always
 * switches: a click with `detail === 0` came from Enter or Space, whatever
 * pointer pressed in the row before it.
 */
export function togglePress(
  pointer: string | null,
  selected: boolean,
  phone: boolean,
  keyboard = false,
): "toggle" | "select" {
  if (keyboard) return "toggle";
  return pointer === "touch" && !selected && !phone ? "select" : "toggle";
}

export interface RailRowProps {
  /** The name. It is the label text and the button's accessible name. */
  label: string;
  /** How many levels deep the row sits. `0` is the top. */
  depth?: number;
  /** Draw a thin vertical guide in each indent step. */
  guides?: boolean;
  /**
   * The row opens and closes. On a hover device its chevron takes the icon's
   * slot on hover and on keyboard focus. On a phone it has its own column.
   */
  expand?: RailRowExpand;
  /**
   * The row sits in a tree. On a phone a leaf keeps the chevron column
   * empty, so its icon lines up with its siblings that can open.
   */
  tree?: boolean;
  /** The marker before the label: an icon, a dot, a state mark. */
  icon?: React.ReactNode;
  /** What the trailing zone shows at rest, such as a count. Muted. */
  meta?: React.ReactNode;
  /** The row's controls. Hidden at rest, shown on hover or focus. */
  actions?: React.ReactNode;
  /**
   * The row's controls on a phone, ALWAYS visible, usually one "···" that
   * opens the row's menu. Nothing can hover there, and selecting a row closes
   * the drawer the rail sits in. Absent: the selected row keeps `actions`.
   */
  phoneActions?: React.ReactNode;
  /** Hold the actions open, for example while the row's menu is open. */
  pinned?: boolean;
  selected?: boolean;
  /** `group` draws the label at medium weight. Default `item`. */
  tier?: RailRowTier;
  onSelect?: () => void;
  /** Takes the label's place: a rename field, or the draft of a new node. */
  editor?: React.ReactNode;
  /** Drag handlers, `onContextMenu`, data attributes. Spread on the row. */
  rowProps?: React.HTMLAttributes<HTMLDivElement> & Record<`data-${string}`, unknown>;
  /** `aria-pressed`, `disabled` and the like, for the label button. */
  buttonProps?: Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onClick" | "type">;
  /** State classes only: a dragged row's opacity, a drop target's ring. */
  className?: string;
}

/** The row's own classes, from its state. Pure, so the test can read it. */
export function railRowClass({
  selected,
  tier,
  touch,
}: {
  selected: boolean;
  tier: RailRowTier;
  touch: boolean;
}): string {
  const height = touch ? "min-h-10" : "min-h-8";
  const tone = selected
    ? "bg-primary/10 text-primary"
    : tier === "group"
      ? "text-foreground hover:bg-muted"
      : "text-muted-foreground hover:bg-muted hover:text-foreground";
  // ⚠️ No `gap` on the row. A flex gap still charges its 4 px beside the
  // zero-width actions zone and beside each indent step, which took 8 px of
  // the room this row exists to give back (measured at a 256 px rail). The
  // phone's chevron column keeps its own margin instead.
  return `group relative flex ${height} items-center rounded-md px-2 tech-transition ${tone}`;
}

export function RailRow({
  label,
  depth = 0,
  guides = false,
  expand,
  tree = false,
  icon,
  meta,
  actions,
  phoneActions,
  pinned = false,
  selected = false,
  tier = "item",
  onSelect,
  editor,
  rowProps,
  buttonProps,
  className = "",
}: RailRowProps) {
  const { isMobile } = useViewMode();
  // Destructured: `ref={result.attachLabel}` would make the React compiler read
  // the whole object as a ref, and refuse each other read of it in render.
  const { attachLabel, onPointerEnter, onPointerLeave, onFocus, onBlur, tip } =
    useOverflowTip(label);
  // On a phone, `phoneActions` (always shown) take the place of `actions`.
  const phoneControls = isMobile && phoneActions != null && !editor;
  const hoverActions = !phoneControls && Boolean(actions) && !editor;
  // A phone cannot hover. With no `phoneActions`, the chosen row keeps its own.
  const held = hoverActions && (pinned || (isMobile && selected));
  // An editor draws its own icon, and no toggle covers it.
  const toggle = expand && !editor ? expand : null;
  // The phone gives the chevron a column of its own and keeps the icon. The
  // in-slot swap is for a device that can hover.
  const ownColumn = isMobile && (tree || Boolean(toggle));
  const swap = Boolean(toggle) && !isMobile;
  // The kind of pointer that last pressed in this row, and when. Taken on
  // the ROW, in the capture phase: a tap's pointerdown lands on the icon,
  // before the hover that switches the chevron on, so the toggle never sees
  // it. A press counts only while it is fresh, and a cancel forgets it, so a
  // pan or a long-press cannot change what a later key does.
  const pressedBy = useRef<string | null>(null);
  const pressedAt = useRef(0);
  const onTogglePress = toggle
    ? (event: React.MouseEvent) => {
        const fresh = performance.now() - pressedAt.current < PRESS_FRESH_MS;
        const by = fresh ? pressedBy.current : null;
        pressedBy.current = null;
        const keyboard = event.detail === 0;
        if (togglePress(by, selected, isMobile, keyboard) === "select") onSelect?.();
        else toggle.onToggle();
      }
    : undefined;
  const onKeyDown = toggle
    ? (event: React.KeyboardEvent) => {
        if (!expandKey(event.key, toggle.expanded)) return;
        event.preventDefault();
        toggle.onToggle();
      }
    : undefined;

  const toggleButton = toggle ? (
    <Button
      variant="ghost"
      size="none"
      radius="keep"
      icon={toggle.expanded ? "ChevronDown" : "ChevronRight"}
      aria-label={toggle.expanded ? `Collapse ${label}` : `Expand ${label}`}
      aria-expanded={toggle.expanded}
      data-rail-toggle=""
      onClick={onTogglePress}
      onKeyDown={onKeyDown}
      className={
        swap
          ? "rail-row-chevron absolute -left-0.5 top-1/2 z-10 h-5 w-5 -translate-y-1/2 rounded"
          : "h-8 w-6 rounded"
      }
    />
  ) : null;

  return (
    <div
      {...rowProps}
      onPointerDownCapture={(event: React.PointerEvent) => {
        pressedBy.current = event.pointerType;
        pressedAt.current = performance.now();
      }}
      onPointerCancelCapture={() => {
        pressedBy.current = null;
      }}
      data-rail-row=""
      data-pinned={held ? "" : undefined}
      // The count gives way only to actions that take its place. A row with
      // no actions keeps its count on hover and on focus.
      data-has-actions={hoverActions ? "" : undefined}
      className={`${railRowClass({ selected, tier, touch: isMobile })} ${className}`}
    >
      {Array.from({ length: depth }, (_, level) => (
        <span key={level} aria-hidden className="relative w-4 shrink-0 self-stretch">
          {guides ? <span className="absolute inset-y-0 left-2 w-px bg-border" /> : null}
        </span>
      ))}
      {ownColumn ? (
        // The phone's chevron column, as the tree drew it before the swap.
        <span className="-ml-1 mr-1 flex w-6 shrink-0 items-center justify-center">
          {toggleButton}
        </span>
      ) : swap ? (
        // A zero-width anchor where the icon starts. The toggle hangs from it
        // over the icon, so the slot is one slot and costs no width.
        <span className="relative w-0 shrink-0 self-stretch">{toggleButton}</span>
      ) : null}
      {editor ?? (
        <button
          type="button"
          {...buttonProps}
          data-rail-label=""
          onClick={() => {
            pressedBy.current = null;
            onSelect?.();
          }}
          onFocus={onFocus}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          className="flex min-w-0 flex-1 items-center gap-2 self-stretch text-left"
        >
          {swap && icon ? (
            // Stays in the button, so its name (a state mark's words) is
            // still part of the row's name while the chevron covers it.
            <span data-rail-icon="" className="rail-row-icon inline-flex shrink-0">
              {icon}
            </span>
          ) : icon ? (
            <span data-rail-icon="" className="inline-flex shrink-0">
              {icon}
            </span>
          ) : null}
          <span
            ref={attachLabel}
            onPointerEnter={onPointerEnter}
            onPointerLeave={onPointerLeave}
            className={`min-w-0 flex-1 truncate text-[0.8125rem] leading-5 ${
              tier === "group" ? "font-medium" : ""
            }`}
          >
            {label}
          </span>
          {meta != null && meta !== false ? (
            <span className="rail-row-meta shrink-0 text-[11px] tabular-nums text-muted-foreground">
              {meta}
            </span>
          ) : null}
        </button>
      )}
      {hoverActions ? (
        <span className="reveal-on-hover rail-row-actions flex shrink-0 items-center gap-0.5">
          {/* The space before the actions, INSIDE the zone, so it folds away
              with them at rest. */}
          <span aria-hidden className="w-0.5 shrink-0" />
          {actions}
        </span>
      ) : phoneControls ? (
        <span data-rail-phone-actions="" className="-mr-2 flex shrink-0 items-center">
          {phoneActions}
        </span>
      ) : null}
      {editor ? null : tip}
    </div>
  );
}

export default RailRow;
