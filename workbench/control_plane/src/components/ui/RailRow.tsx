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
 *     [indent + guides][lead][icon  label ……………  meta][actions]
 *
 * - **indent** — one 12 px step per level, each with an optional thin
 *   vertical guide, so a nested level reads as nested.
 * - **lead** — an optional control before the icon, such as an expand
 *   chevron. `leadSpace` keeps its width when a row has none, so siblings
 *   start their icons at one x.
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
 * pins its actions, and every row grows to 40 px for a finger.
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
import { useOverflowTip } from "@/components/ui/OverflowTip";

/** One indent step, in px. The tree's own step before this primitive. */
export const RAIL_INDENT_PX = 12;

/** A level that groups (a space, a section head) or an item in it. */
export type RailRowTier = "group" | "item";

export interface RailRowProps {
  /** The name. It is the label text and the button's accessible name. */
  label: string;
  /** How many levels deep the row sits. `0` is the top. */
  depth?: number;
  /** Draw a thin vertical guide in each indent step. */
  guides?: boolean;
  /** A control before the icon, such as the expand chevron. */
  lead?: React.ReactNode;
  /** Keep the lead's width when `lead` is absent, so siblings align. */
  leadSpace?: boolean;
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
  lead,
  leadSpace = false,
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
      {lead || leadSpace ? (
        <span className="mr-1 flex w-4.5 shrink-0 items-center justify-center">{lead}</span>
      ) : null}
      {editor ?? (
        <button
          type="button"
          {...buttonProps}
          onClick={onSelect}
          onFocus={onFocus}
          onBlur={onBlur}
          className="flex min-w-0 flex-1 items-center gap-2 self-stretch text-left"
        >
          {icon}
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
