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
 * - **indent** — one 12 px step per level, each with an optional thin
 *   vertical guide, so a nested level reads as nested.
 * - **icon** — the row's marker. On a row that can open (`expand`), the
 *   expand chevron takes the SAME slot on hover and on keyboard focus, and
 *   the icon hides. ClickUp does this, and it removes the chevron column,
 *   which took 22 px from every name. The toggle is a real button at all
 *   times. At rest it is transparent, but Tab reaches it, and its label and
 *   `aria-expanded` are always there. On a phone it shows the chevron in the
 *   slot at all times, because nothing can hover.
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
 * phone (`useViewMode().isMobile`) nothing can hover, so the SELECTED row
 * pins its actions, an expandable row shows its chevron, and every row
 * grows to 40 px for a finger.
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

export interface RailRowProps {
  /** The name. It is the label text and the button's accessible name. */
  label: string;
  /** How many levels deep the row sits. `0` is the top. */
  depth?: number;
  /** Draw a thin vertical guide in each indent step. */
  guides?: boolean;
  /**
   * The row opens and closes. Its chevron takes the icon's slot on hover and
   * on keyboard focus (on a phone, at all times).
   */
  expand?: RailRowExpand;
  /** The marker before the label: an icon, a dot, a state mark. */
  icon?: React.ReactNode;
  /** What the trailing zone shows at rest, such as a count. Muted. */
  meta?: React.ReactNode;
  /** The row's controls. Hidden at rest, shown on hover or focus. */
  actions?: React.ReactNode;
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
  // lead keeps its own margin instead.
  return `group relative flex ${height} items-center rounded-md px-2 tech-transition ${tone}`;
}

export function RailRow({
  label,
  depth = 0,
  guides = false,
  expand,
  icon,
  meta,
  actions,
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
  // A phone cannot hover, so the row the member chose keeps its controls.
  const held = Boolean(actions) && (pinned || (isMobile && selected));
  // The toggle sits over the icon's slot, so it draws only where the icon
  // does. An editor draws its own icon, and no toggle covers it.
  const toggle = expand && !editor ? expand : null;
  const onKeyDown = toggle
    ? (event: React.KeyboardEvent) => {
        if (!expandKey(event.key, toggle.expanded)) return;
        event.preventDefault();
        toggle.onToggle();
      }
    : undefined;

  return (
    <div
      {...rowProps}
      data-rail-row=""
      data-pinned={held ? "" : undefined}
      className={`${railRowClass({ selected, tier, touch: isMobile })} ${className}`}
    >
      {Array.from({ length: depth }, (_, level) => (
        <span key={level} aria-hidden className="relative w-3 shrink-0 self-stretch">
          {guides ? <span className="absolute inset-y-0 left-2 w-px bg-border" /> : null}
        </span>
      ))}
      {toggle ? (
        // A zero-width anchor where the icon starts. The toggle hangs from it
        // over the icon, so the slot is one slot and costs no width.
        <span className="relative w-0 shrink-0 self-stretch">
          <Button
            variant="ghost"
            size="none"
            radius="keep"
            icon={toggle.expanded ? "ChevronDown" : "ChevronRight"}
            aria-label={toggle.expanded ? `Collapse ${label}` : `Expand ${label}`}
            aria-expanded={toggle.expanded}
            data-rail-toggle=""
            onClick={toggle.onToggle}
            onKeyDown={onKeyDown}
            className={`absolute -left-0.5 top-1/2 z-10 h-5 w-5 -translate-y-1/2 rounded ${
              isMobile ? "" : "rail-row-chevron"
            }`}
          />
        </span>
      ) : null}
      {editor ?? (
        <button
          type="button"
          {...buttonProps}
          onClick={onSelect}
          onFocus={onFocus}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          className="flex min-w-0 flex-1 items-center gap-2 self-stretch text-left"
        >
          {toggle && icon ? (
            // Stays in the button, so its name (a state mark's words) is
            // still part of the row's name while the chevron covers it.
            <span
              data-rail-icon=""
              className={`inline-flex shrink-0 ${isMobile ? "opacity-0" : "rail-row-icon"}`}
            >
              {icon}
            </span>
          ) : (
            icon
          )}
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
      {actions && !editor ? (
        <span className="reveal-on-hover rail-row-actions flex shrink-0 items-center gap-0.5">
          {/* The space before the actions, INSIDE the zone, so it folds away
              with them at rest. */}
          <span aria-hidden className="w-0.5 shrink-0" />
          {actions}
        </span>
      ) : null}
      {editor ? null : tip}
    </div>
  );
}

export default RailRow;
