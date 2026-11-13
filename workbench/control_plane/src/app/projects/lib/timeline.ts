/**
 * Projects · the timeline, as arithmetic (WS-27t).
 *
 * A Gantt chart is bar geometry plus two rules that are not geometry at all,
 * and both were decisions rather than defaults:
 *
 * * **D-PM-11 — what earns a bar.** Hierarchy depth: top-level tasks get rows,
 *   subtasks fold into their parent and expand on demand. Paca's Timeline
 *   pre-filters to a reserved `Epic` type instead; that was rejected because
 *   `pm_task_types` is per-project data with no reserved names (D-PM-2), so
 *   "Epic" would have to become either a seeded row every project inherits or
 *   a name-match that silently stops working the day somebody renames a type.
 *   `parent_task_id` already says what depth means and cannot be renamed.
 *
 * * **D-PM-12 — an arrow WARNS, it does not push.** A `blocks` edge whose
 *   blocker finishes after the blocked task starts is drawn in the danger tone
 *   and says so. Nothing is rescheduled. Jira drags the dependents forward;
 *   that was rejected because it contradicts WS-27p's "derived and shown, never
 *   enforced" and turns one drag into an unbounded cascade of real `PATCH`es,
 *   each with its own activity row and notification.
 *
 * **The generic maths moved to `@/components/timeline` (STU-D6):** the scale,
 * the bar and column geometry, the arrow paths and the drag date maths. This
 * file re-exports them, so each import of `lib/timeline` still works. What is
 * left here reads a task: its dates, its depth and its dependencies.
 *
 * Dates are `YYYY-MM-DD` keys throughout, for the reason `lib/calendar.ts`
 * gives at length: `new Date("2026-08-07")` is midnight UTC, which is the 6th
 * anywhere west of Greenwich.
 */

import { treeRows } from "@/lib/taskTree";
import type { Bar } from "@/components/timeline/geometry";
import { barForRow, rangeForRows } from "@/components/timeline/rows";
import {
  DEFAULT_ZOOM,
  unionSpan,
  type TimelineRange,
  type TimelineWindow,
  type TimelineZoom,
} from "@/components/timeline/scale";
import { clampEndDay, clampStartDay, orderedSpan } from "@/components/timeline/drag";

import { dayKey, fromDayKey } from "./calendar";
import { interval, rowInterval, taskToBlockRow } from "./timelineAdapter";
import { dueInstantForDay } from "./quickAdd";

import type { TaskRow } from "./api";

export { interval, rowInterval };
export {
  CHANNEL_PX,
  CONTROL_SHIELD_PX,
  CORNER_R,
  AIM_KEEP_PX,
  EDGE_GRACE_MS,
  EDGE_HIT_PX,
  EDGE_STUB_PX,
  edgeHitOrder,
  edgeMidpoint,
  edgePath,
  edgePoints,
  polylineLength,
  roundedPath,
  stagger,
} from "@/components/timeline/deps";
export type { Point, RoutedEdge } from "@/components/timeline/deps";
export {
  BAR_H,
  LABEL_GAP_PX,
  LABEL_INSIDE_PX,
  LABEL_MAX_PX,
  LABEL_MIN_PX,
  MIN_BAR_PX,
  ROW_H,
  barForSpan,
  dayAtPx,
  dayCells,
  dayPx,
  dayStep,
  isWeekend,
  labelSide,
  monthCells,
  weekCells,
} from "@/components/timeline/geometry";
export type {
  Bar,
  DayCell,
  LabelPlacement,
  MonthCell,
  WeekCell,
} from "@/components/timeline/geometry";
export {
  DEFAULT_ZOOM,
  PAD_DAYS,
  PX_PER_DAY,
  WINDOW_LIMIT_DAYS,
  ZOOMS,
  ZOOM_ORDER,
  windowCentre,
  windowFor,
  windowIncluding,
} from "@/components/timeline/scale";
export type {
  TimelineRange,
  TimelineWindow,
  TimelineZoom,
  ZoomSpec,
} from "@/components/timeline/scale";

export interface TimelineRow {
  task: TaskRow;
  depth: number;
  /**
   * EVERY subtask under this row present in the window, at any depth. The
   * row's bar folds in their dates (`rowInterval`), so a parent whose only
   * dated work is a grandchild still draws a derived bar.
   */
  children: TaskRow[];
  /**
   * The DIRECT subtask rows, each with its own `subrows` — drawn when this
   * row is expanded. B3: this used to be one level only, so a grandchild
   * was never drawn at all.
   */
  subrows: TimelineRow[];
  /**
   * A subtask whose parent is not in the window. It is promoted to a row of
   * its own and carries the "↳ Parent" crumb (D-PM-38 decision 3).
   */
  orphan: boolean;
}

export interface Edge {
  id: string;
  blocker_id: string;
  blocked_id: string;
}

/**
 * Group the window's tasks into rows by hierarchy depth (D-PM-11).
 *
 * **A subtask whose parent is not in the window is promoted to a row of its
 * own**, rather than hidden under a parent that is not there. Hiding it would
 * make a filtered timeline silently drop work — the same failure the `undated`
 * count exists to prevent, one level down.
 *
 * **Every level nests (B3, fixed in Subtasks S3).** The tree is the shared
 * `@/lib/taskTree` walk, so depth, orphans and a loop in the data are decided
 * the same way the list and the table decide them. Before S3 this grouped one
 * level only: a grandchild went under a child that was never a row, so the
 * grandchild was never drawn.
 */
export function timelineRows(tasks: readonly TaskRow[]): TimelineRow[] {
  const roots: TimelineRow[] = [];
  // The open path from the root to the row being placed. `treeRows` walks in
  // pre-order, so a row's parent is always the entry one level up.
  const path: TimelineRow[] = [];
  for (const tree of treeRows(tasks)) {
    path.length = tree.depth;
    const row: TimelineRow = {
      task: tree.task,
      depth: tree.depth,
      children: [],
      subrows: [],
      orphan: tree.orphan,
    };
    // Every ancestor's bar folds this row's dates in.
    for (const ancestor of path) ancestor.children.push(tree.task);
    if (tree.depth === 0) roots.push(row);
    else path[tree.depth - 1].subrows.push(row);
    path.push(row);
  }
  return roots;
}

/**
 * The rows the rail draws, in order: each row, then its subrows when it is
 * expanded, at EVERY depth (B3).
 *
 * An explicit stack, like `taskTree`, so depth is limited by memory and not
 * by the call stack. A collapsed row hides its whole subtree.
 */
export function openTimelineRows(
  source: readonly TimelineRow[],
  expanded: ReadonlySet<string>,
): TimelineRow[] {
  const out: TimelineRow[] = [];
  const stack = [...source].reverse();
  while (stack.length) {
    const row = stack.pop()!;
    out.push(row);
    if (expanded.has(row.task.id)) {
      for (let i = row.subrows.length - 1; i >= 0; i -= 1) stack.push(row.subrows[i]);
    }
  }
  return out;
}

/**
 * ── BANDS (WS-27t S5) ─────────────────────────────────────────────────────
 *
 * A grouped timeline is bands of rows — the same idea the list draws as headed
 * sections, and it reads the same `groupBy`, so a view grouped by project on
 * the board opens grouped by project here.
 *
 * **An empty band is dropped.** The board keeps an empty status column, because
 * a missing column there reads as a missing state and the column is a drop
 * target. A timeline band is neither: it is a heading with no rows under it,
 * and on a canvas whose vertical space is already the scarce axis it is a line
 * of nothing. The list drops them for the same reason.
 */
export interface TimelineBand {
  key: string;
  label: string;
  rows: TimelineRow[];
  /** Everything in the band, as one interval — the bar drawn when collapsed. */
  span: { from: string; to: string } | null;
  /** How many tasks the band holds, INCLUDING folded subtasks. */
  count: number;
}

/**
 * The interval covering every task in a set of rows, subtasks included.
 *
 * What a collapsed band draws. Collapsing is otherwise pure loss — the rows go
 * and nothing takes their place — whereas a roll-up bar turns it into the
 * summarising gesture it should be: twelve rows become one line that still says
 * when the group runs. MS Project's summary task, and the same arithmetic
 * `rowInterval` already does one level down.
 *
 * `null` when nothing in the band has a date, which is the honest answer — a
 * band of unscheduled work has no span to draw, and inventing one from the
 * chart's own edges would be a bar nobody's dates produced.
 */
export function bandSpan(
  rows: readonly TimelineRow[],
): { from: string; to: string } | null {
  return unionSpan(
    rows
      .map((row) => rowInterval(row.task, row.children))
      .filter(Boolean) as { from: string; to: string }[],
  );
}

/** Group output → the bands the chart draws, empty ones dropped. */
export function timelineBands(
  groups: readonly { key: string; label: string; tasks: TaskRow[] }[],
): TimelineBand[] {
  const out: TimelineBand[] = [];
  for (const group of groups) {
    if (group.tasks.length === 0) continue;
    const rows = timelineRows(group.tasks);
    out.push({
      key: group.key,
      label: group.label,
      rows,
      span: bandSpan(rows),
      // The group's own total, not the row count: a subtask folded into its
      // parent is still a task in this band, and a count that disagreed with
      // the board's for the same group would be the tell that there are two
      // groupings.
      count: group.tasks.length,
    });
  }
  return out;
}

/**
 * The date range the chart covers, for these rows.
 *
 * The scale itself is the block's `rangeForSpans`. This only says what each
 * row's span is, folding in its children.
 */
export function timelineRange(
  rows: readonly TimelineRow[],
  todayKey: string,
  zoom: TimelineZoom = DEFAULT_ZOOM,
  window?: TimelineWindow,
): TimelineRange {
  return rangeForRows(
    rows.map((r) => taskToBlockRow(r.task, r.children)),
    todayKey,
    zoom,
    window,
  );
}

/**
 * Where a row's bar sits, or `null` when it has no dates anywhere.
 *
 * The geometry is the block's `barForRow`. The row is the task with its
 * children folded in, through the adapter.
 */
export function bar(
  task: TaskRow,
  children: readonly TaskRow[],
  range: TimelineRange,
): Bar | null {
  return barForRow(taskToBlockRow(task, children), range);
}

/**
 * Does this dependency disagree with the schedule? (D-PM-12)
 *
 * A `blocks` edge asserts the blocker finishes before the blocked task starts.
 * It is violated when the blocker's END is strictly after the blocked task's
 * START — the two overlap, so the sequence the arrow claims cannot happen.
 *
 * **Equal dates are NOT a conflict.** A blocker due on the 10th and a task
 * starting on the 10th is the normal way people schedule a handover; flagging
 * it would make the warning fire on half a healthy plan and be ignored within a
 * week.
 *
 * **An edge with a date missing on either end is not a conflict either** — it
 * is unknowable, and a warning that fires on absent data teaches people that
 * the warning means nothing.
 *
 * **A finished blocker never conflicts.** It has already happened; the dates
 * are history, and WS-27p's rule that a resolved blocker blocks nothing applies
 * to the warning exactly as it applies to the badge.
 */
export function conflicts(
  blocker: Pick<TaskRow, "start_date" | "due_at" | "completed_at">,
  blocked: Pick<TaskRow, "start_date" | "due_at">,
): boolean {
  if (blocker.completed_at) return false;
  const before = interval(blocker as TaskRow);
  const after = interval(blocked as TaskRow);
  if (!before || !after) return false;
  return before.to > after.from;
}

/** A one-sentence explanation of a conflict, for the warning's title. */
export function conflictLabel(blockerTitle: string): string {
  return `Starts before "${blockerTitle}" is due to finish. Nothing has been ` +
    `rescheduled — the dates are yours to fix.`;
}

/**
 * ── WHAT A DRAG WRITES (WS-27t S2) ────────────────────────────────────────
 *
 * Three gestures, three patches, all of them one `PATCH /tasks/{id}` through
 * the page's existing `moveTask` — the same seam the calendar's drag already
 * uses, so a date moved on the timeline produces the same activity row, the
 * same validation and the same revert as one moved on the calendar.
 *
 * **A move reuses `rescheduleTo` outright** rather than growing a second
 * whole-bar shift here. That function already owns the two rules this needed —
 * the span is preserved, and the due TIME of day survives the move — and both
 * are rules a second implementation would get wrong in the same order.
 *
 * ⚠️ **Dragging still never touches anything but the task you dragged.**
 * D-PM-12 says an arrow warns and does not push, and that survives here: move a
 * blocker onto its dependent and the arrow turns red, exactly as it would if
 * you had typed the date. Nothing cascades.
 */

/** A due instant on `day`, keeping the time of day the task already had. */
function dueAtOnDay(day: string, previous: string | null | undefined): string {
  if (!previous) return dueInstantForDay(day);
  const had = new Date(previous);
  const moved = fromDayKey(day);
  moved.setHours(had.getHours(), had.getMinutes(), had.getSeconds(), had.getMilliseconds());
  return moved.toISOString();
}

/**
 * ⚠️ **A resize moves ONE edge and PINS the other.** This is the whole rule,
 * and getting it half-right is the bug the owner hit on 2026-08-31.
 *
 * A task with only a due date draws as a one-day bar on that day. Pull its
 * right edge three days out and the obvious implementation writes
 * `due_at += 3` — which is correct as far as it goes, and completely wrong as a
 * gesture: the task still has no start date, so it is still a ONE-DAY bar,
 * three days later. The bar does not grow. **It jumps.** The same defect
 * mirrors on a start-only task pulled by its left edge.
 *
 * So when the far edge has no stored date, the resize writes it, using where
 * the bar already is. Extending a point makes a span; it never relocates the
 * point. `previewBar` in the view has always drawn it this way — which is why
 * the bar looked right during the drag and moved on release.
 */

/**
 * Drag the LEFT edge to `day` — the task starts then.
 *
 * **Clamped, never swapped.** Pushing the start past the due date could either
 * flip the interval or stop at it; stopping is right, because a bar that turns
 * inside out under the cursor is not something anybody meant to ask for, and
 * the inverted span would then be written as real dates.
 */
export function resizeStart(
  task: TaskRow,
  day: string,
): { start_date: string; due_at?: string } | null {
  const span = interval(task);
  if (!span) return null;
  const capped = clampStartDay(span, day);
  const current = task.start_date ? task.start_date.slice(0, 10) : null;
  if (capped === current) return null;
  // Pin the right edge. Without a stored `due_at` the bar's right edge is only
  // implied by the start, so moving the start would carry it along.
  return task.due_at
    ? { start_date: capped }
    : { start_date: capped, due_at: dueInstantForDay(span.to) };
}

/**
 * Drag the RIGHT edge to `day` — the task is due then.
 *
 * Clamped at the start for the same reason. The time of day is preserved when
 * there was one.
 */
export function resizeEnd(
  task: TaskRow,
  day: string,
): { due_at: string; start_date?: string } | null {
  const span = interval(task);
  if (!span) return null;
  const floored = clampEndDay(span, day);
  const current = task.due_at ? dayKey(new Date(task.due_at)) : null;
  if (floored === current) return null;
  const due_at = dueAtOnDay(floored, task.due_at);
  // Pin the left edge — the case the owner reported.
  return task.start_date ? { due_at } : { due_at, start_date: span.from };
}

/**
 * Draw a span on an UNSCHEDULED row — the task runs `from` to `to`.
 *
 * The row of a task with no dates is otherwise dead space carrying the word
 * "unscheduled", which names the problem and offers nothing. Dragging across it
 * is the shortest path from "no dates" to "these dates", and it writes both
 * ends at once so the task never passes through a half-scheduled state.
 */
export function spanFor(
  from: string,
  to: string,
): { start_date: string; due_at: string } {
  const span = orderedSpan(from, to);
  return { start_date: span.from, due_at: dueInstantForDay(span.to) };
}

/**
 * Why this bar cannot be dragged, or `null` when it can.
 *
 * A DERIVED bar has no dates of its own — the span is its children's — so there
 * is no field for a drag to write. Dragging it would have to either invent
 * dates on the parent or silently rewrite every child, and both are worse than
 * refusing. This is the same rule the link handle already applied, said once
 * and now read by both.
 */
export function dragRefusal(barToDrag: Bar | null): string | null {
  if (!barToDrag) return "This task has no dates to move.";
  if (barToDrag.derived) {
    return "These dates come from the subtasks. Move those instead.";
  }
  return null;
}

/**
 * May this drag create a link?
 *
 * Only the cheap, local refusals — a task cannot block itself, and an edge that
 * already exists is a no-op rather than a duplicate. **The cycle check is NOT
 * duplicated here**: `assert_no_block_cycle` owns it, bounded and tested, and a
 * second implementation in the browser would be the one that drifts. The drop
 * posts and reports the gateway's own refusal message.
 */
export function canLink(
  blockerId: string,
  blockedId: string,
  existing: readonly Edge[],
): { ok: true } | { ok: false; reason: string } {
  if (blockerId === blockedId) {
    return { ok: false, reason: "A task cannot block itself." };
  }
  if (existing.some((e) => e.blocker_id === blockerId && e.blocked_id === blockedId)) {
    return { ok: false, reason: "That dependency is already there." };
  }
  return { ok: true };
}
