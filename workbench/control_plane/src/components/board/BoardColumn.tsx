"use client";

/**
 * One lane of a Kanban board — the one column of Projects, My Tasks and the
 * CRM (WS-53 CRM-C2, `crm_platform.md` §20.5).
 *
 * The owner saw three boards that drew one object three ways: two widths, two
 * radii, two fills, and a colour cap that the round corner clipped. This file
 * is now the only place a lane gets its shape. `src/lib/boards.test.ts` fails
 * a board that draws a column of its own, in any class order.
 *
 * ## The shape
 *
 * - **The frame** is `w-72 rounded-lg border border-border bg-muted/30 p-2`,
 *   and it stays neutral. The cards (`TaskCardShell`, `bg-card`) stand on it.
 * - **The colour** lives in the header's `StageChip`, inset in the frame's
 *   padding. No stripe, so no corner can clip it.
 * - **A drop target** adds `ring-2 ring-primary/40`. A ring takes no layout
 *   space, so the frame keeps its size during a drag. The ring follows the
 *   member's accent, and the lane hue does not.
 * - **An empty lane** shows a dashed `rounded-md` box with the `empty` text.
 * - **The body** scrolls down when the board gives the lane a height (My
 *   Tasks, the CRM). The board scrolls across (`Board.tsx`).
 *
 * WHAT a card shows, and what a drop does, stays each app's business. Pass the
 * drag handlers as props: they land on the frame.
 */

import type React from "react";

import { QuickAdd } from "@/components/QuickAdd";

import { StageChip, type StageHeader } from "./StageChip";

export type { StageHeader } from "./StageChip";

type FrameProps = Omit<React.HTMLAttributes<HTMLElement>, "children" | "className" | "title">;

export interface BoardColumnProps extends FrameProps {
  /** The chip of the lane. `null` draws a lane with no header (a swimlane cell). */
  header: StageHeader | null;
  /** A line under the chip, for the CRM money totals. */
  totals?: React.ReactNode;
  /** The text of an empty lane. The caller sets it only while the lane is empty. */
  empty?: React.ReactNode;
  /** A card is over this lane, and the lane accepts it. */
  dropTarget?: boolean;
  /** Draws "+ Add" at the foot. The shared `QuickAdd` collects the title. */
  onAdd?: (title: string) => Promise<void>;
  /** The name of the add control, for example `Add to “In progress”`. */
  addLabel?: string;
  /** Controls at the right end of the header. */
  headerActions?: React.ReactNode;
  /**
   * The lane is hidden from the board for most members (WS-53 CRM-C3). It
   * draws a quiet "Hidden" word beside the chip. CRM-C3 wires it.
   */
  hidden?: boolean;
  /** An absolute layer over the whole lane, for a refused drop. */
  overlay?: React.ReactNode;
  /** The body element. A board that draws `<li>` cards asks for `ul`. */
  as?: "div" | "ul";
  children?: React.ReactNode;
}

export function BoardColumn({
  header,
  totals,
  empty,
  dropTarget = false,
  onAdd,
  addLabel = "Add",
  headerActions,
  hidden = false,
  overlay,
  as = "div",
  children,
  ...frame
}: BoardColumnProps) {
  const Body = as;
  const EmptyBox = as === "ul" ? "li" : "div";
  return (
    <section
      aria-label={header?.title}
      {...frame}
      className={[
        "tech-transition relative flex w-72 shrink-0 flex-col rounded-lg border border-border bg-muted/30 p-2",
        dropTarget ? "ring-2 ring-primary/40" : "",
      ].join(" ")}
    >
      {overlay}
      {header ? <BoardColumnHead header={header} actions={headerActions} hidden={hidden} /> : null}
      {totals ? <div className="px-1 pb-2 text-[10px] text-muted-foreground">{totals}</div> : null}
      {/* The negative margin takes the body to the frame's inner edge, and
          its own padding gives the card's cursor ring and hover shadow room.
          A scroller clips what leaves it, and `px-2` is that room. */}
      <Body className="-mx-2 flex min-h-0 flex-1 flex-col space-y-1 overflow-y-auto px-2 py-1">
        {children}
        {empty ? (
          <EmptyBox className="rounded-md border border-dashed border-border p-3 text-center text-xs text-muted-foreground">
            {empty}
          </EmptyBox>
        ) : null}
      </Body>
      {onAdd ? (
        <div className="pt-1">
          <QuickAdd label={addLabel} onAdd={onAdd} />
        </div>
      ) : null}
    </section>
  );
}

/** The header row of a lane: the chip, and the actions at the right. */
function BoardColumnHead({
  header,
  actions,
  hidden,
}: {
  header: StageHeader;
  actions?: React.ReactNode;
  hidden: boolean;
}) {
  return (
    <header className="flex min-w-0 items-center gap-2 pb-2">
      <StageChip {...header} />
      {hidden ? <span className="shrink-0 text-[10px] text-muted-foreground">Hidden</span> : null}
      {actions ? <div className="ml-auto flex shrink-0 items-center gap-1">{actions}</div> : null}
    </header>
  );
}

/**
 * One column head outside a lane — the Projects swimlane header row.
 *
 * That row draws the column heads once, above every swimlane. Each head is the
 * width of a lane, with the same 1 px edge and the same padding, so its chip
 * stands over the lane's cards exactly where a lane's own chip would.
 */
export function BoardHeaderCell({ header }: { header: StageHeader }) {
  return (
    <div className="w-72 shrink-0 border border-transparent px-2">
      <StageChip {...header} />
    </div>
  );
}
