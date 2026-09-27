/**
 * Projects · how a canvas draws a subtask (D-PM-38, spec §12.9, slice S3).
 *
 * Each view has one Subtasks setting: Nested, Separate or Hidden. This module
 * owns the three questions every canvas would otherwise answer on its own:
 *
 * 1. **Which modes a canvas offers, and its default.** The list and the table
 *    offer all three and default to Nested. The board offers Separate and
 *    Hidden and defaults to Separate (owner decision 1). The Projects calendar
 *    offers Separate and Hidden and defaults to Separate. The timeline is
 *    always Nested (D-PM-11), so it offers no control.
 * 2. **The rows a canvas draws** for one task set and one mode. The list and
 *    the table both call `subtaskSections`, so one input gives one row set on
 *    both. `subtaskView.test.ts` renders the two components and compares.
 * 3. **The counts.** A group counts every task in it, whatever is collapsed
 *    (B7). A board column counts the cards it draws, and its tooltip names how
 *    many of them are subtasks.
 *
 * Pure, so each rule is an assertion rather than a screenshot.
 */

import { type TreeRow, treeRows } from "@/lib/taskTree";

import type { TaskRow, ViewRow } from "./api";
import type { ViewMode } from "./commands";
import { type SubtaskMode, asSubtaskMode } from "./grouping";

/** The control's words. One label per mode, on every canvas that offers it. */
export const SUBTASK_MODE_LABELS: Record<SubtaskMode, string> = {
  nested: "Subtasks: nested",
  separate: "Subtasks: separate",
  hidden: "Subtasks: hidden",
};

const ALL: readonly SubtaskMode[] = ["nested", "separate", "hidden"];
const FLAT: readonly SubtaskMode[] = ["separate", "hidden"];
const NONE: readonly SubtaskMode[] = [];

/**
 * The modes each canvas offers. An empty list means "no control": the
 * timeline is Nested by D-PM-11, and the overview draws no task rows.
 *
 * ⚠️ The board does not offer Nested. A card inside a card is a design that
 * §12.9 does not give, and the owner's default for the board is Separate.
 */
const OFFERED: Record<ViewMode, readonly SubtaskMode[]> = {
  overview: NONE,
  board: FLAT,
  list: ALL,
  table: ALL,
  calendar: FLAT,
  timeline: NONE,
};

const DEFAULTS: Record<ViewMode, SubtaskMode> = {
  overview: "nested",
  board: "separate",
  list: "nested",
  table: "nested",
  calendar: "separate",
  timeline: "nested",
};

/** The modes the Subtasks control offers on this canvas. Empty = no control. */
export function subtaskModesFor(mode: ViewMode): readonly SubtaskMode[] {
  return OFFERED[mode];
}

/** The canvas's own default, used when the view and the member have none. */
export function defaultSubtaskMode(mode: ViewMode): SubtaskMode {
  return DEFAULTS[mode];
}

/**
 * The mode a canvas draws in.
 *
 * `stored` is the member's choice (the overlay) or the view's, or `null`. A
 * stored mode that this canvas does not offer falls back to its default: a
 * list set to Nested and then opened as a board draws Separate cards, and
 * the stored Nested comes back when the member returns to the list.
 */
export function effectiveSubtaskMode(
  stored: SubtaskMode | null | undefined,
  mode: ViewMode,
): SubtaskMode {
  const offered = OFFERED[mode];
  return stored && offered.includes(stored) ? stored : DEFAULTS[mode];
}

/**
 * The Subtasks mode stored for this member on this view, or `null`.
 *
 * **The member's overlay wins, then the view.** §12.9: "the view stores it,
 * and the member's overlay remembers it for that member". So a shared view
 * saved as Nested opens Nested for everybody until a member picks another
 * mode, and then it opens in that member's mode for that member only.
 */
export function storedSubtasks(
  view: Pick<ViewRow, "config" | "user_state"> | null | undefined,
): SubtaskMode | null {
  if (!view) return null;
  return (
    asSubtaskMode(view.user_state?.subtasks) ?? asSubtaskMode(view.config?.subtasks)
  );
}

/**
 * The member's overlay with a new Subtasks mode, EVERY other key kept.
 *
 * The overlay endpoint is a PUT of the whole overlay (`views.set_view_state`),
 * so a body of `{subtasks}` alone would wipe the member's other keys.
 */
export function overlayWithSubtasks(
  userState: Record<string, unknown> | null | undefined,
  next: SubtaskMode,
): Record<string, unknown> {
  return { ...(userState ?? {}), subtasks: next };
}

/**
 * Does this canvas ask the server for top-level tasks only?
 *
 * Only Hidden does. Paging happens in SQL, so the fold must happen there
 * too, or a page of fifty would draw thirty rows.
 */
export function sendsTopLevel(
  stored: SubtaskMode | null | undefined,
  mode: ViewMode,
): boolean {
  return effectiveSubtaskMode(stored, mode) === "hidden";
}

type Task = Pick<TaskRow, "id" | "parent_task_id">;

/**
 * The tasks a flat canvas (the board, the calendar) draws.
 *
 * Hidden drops every subtask. The server already sent `top_level`, and this
 * repeats it on the client so a held page, painted before the new read lands,
 * cannot flash the subtasks back.
 */
export function visibleTasks<T extends Task>(
  tasks: readonly T[],
  mode: SubtaskMode,
): T[] {
  return mode === "hidden"
    ? tasks.filter((task) => !task.parent_task_id)
    : [...tasks];
}

/** One drawn row of the list or the table. */
export interface SubtaskRow<T extends Task = Task> extends TreeRow<T> {
  /**
   * Draw the "↳ Parent" crumb on this row. True for every subtask in
   * Separate mode, and for an ORPHAN in Nested mode (a subtask whose parent
   * is not in the set). A nested row does not need one: its indent and its
   * CornerDownRight mark say where it sits.
   */
  crumb: boolean;
}

/**
 * One task set → the rows a list or a table draws, in one mode.
 *
 * - **Nested**: through `treeRows`, at any depth. `collapsed` hides a parent's
 *   subtree. An orphan sits at the top level and carries its crumb (owner
 *   decision 3). There are no greyed context rows.
 * - **Separate**: flat, in the incoming order. Each subtask carries its crumb.
 * - **Hidden**: flat, top-level tasks only.
 */
export function subtaskRows<T extends Task>(
  tasks: readonly T[],
  mode: SubtaskMode,
  collapsed: ReadonlySet<string> = new Set(),
): SubtaskRow<T>[] {
  if (mode === "nested") {
    return treeRows(tasks, collapsed).map((row) => ({ ...row, crumb: row.orphan }));
  }
  const kept = visibleTasks(tasks, mode);
  const present = new Set(kept.map((task) => task.id));
  return kept.map((task) => {
    const parent = task.parent_task_id ?? null;
    return {
      task,
      depth: 0,
      childCount: 0,
      descendantCount: 0,
      orphan: Boolean(parent) && !present.has(parent!),
      crumb: Boolean(parent),
    };
  });
}

/** A group, as the list and the table draw it. */
export interface SubtaskSection<T extends Task = Task> {
  key: string;
  label: string;
  /**
   * Every task in the group that this mode shows, whatever is collapsed (B7).
   * Counted from the data, never from the rows drawn: a collapsed parent
   * still holds its subtasks, and the heading must not say fewer.
   */
  count: number;
  rows: SubtaskRow<T>[];
}

/**
 * The groups → the sections the list and the table draw.
 *
 * ⚠️ **ONE function for both canvases.** The list and the table must produce
 * the same row set for the same input. Two walks would drift the first time
 * one of them learned a rule the other did not. `order` is the only thing
 * that may differ: the table keeps the server's order under a header sort.
 *
 * An empty group is dropped, which both canvases already did.
 */
export function subtaskSections<T extends Task>(
  groups: readonly { key: string; label: string; tasks: readonly T[] }[],
  mode: SubtaskMode,
  collapsed: ReadonlySet<string>,
  order: (tasks: readonly T[]) => readonly T[] = (tasks) => tasks,
): SubtaskSection<T>[] {
  const out: SubtaskSection<T>[] = [];
  for (const group of groups) {
    const shown = visibleTasks(order(group.tasks), mode);
    if (shown.length === 0) continue;
    out.push({
      key: group.key,
      label: group.label,
      count: shown.length,
      rows: subtaskRows(shown, mode, collapsed),
    });
  }
  return out;
}

/**
 * The member's collapsed parents, with a filter's auto-open.
 *
 * **When a filter is active, a parent with matching children opens by
 * itself.** A filter that matched a subtask and then hid it under a closed
 * parent would look like a filter that matched nothing. So a NEW filter opens
 * every parent. The member may close one again, and that holds until the
 * filter changes. `fold.key` is the filter the member's collapse was made
 * under.
 */
export interface Fold {
  key: string;
  ids: ReadonlySet<string>;
}

const NOTHING: ReadonlySet<string> = new Set();

export function collapsedNow(
  fold: Fold,
  filterKey: string,
  filtered: boolean,
): ReadonlySet<string> {
  if (fold.key === filterKey) return fold.ids;
  return filtered ? NOTHING : fold.ids;
}

/** A collapse toggle, made under the filter now in force. */
export function toggleFold(
  fold: Fold,
  filterKey: string,
  filtered: boolean,
  taskId: string,
): Fold {
  const next = new Set(collapsedNow(fold, filterKey, filtered));
  if (next.has(taskId)) next.delete(taskId);
  else next.add(taskId);
  return { key: filterKey, ids: next };
}

/**
 * A board column's count tooltip: "5 tasks (2 subtasks) in To do".
 *
 * The count is the cards drawn, so it agrees with what the member sees. The
 * parenthesis says how many of them are subtasks, and it is left out when
 * there are none.
 */
export function columnCountTitle(
  tasks: readonly Task[],
  label: string,
): string {
  const n = tasks.length;
  const m = tasks.filter((task) => task.parent_task_id).length;
  const base = `${n} task${n === 1 ? "" : "s"}`;
  const subs = m > 0 ? ` (${m} subtask${m === 1 ? "" : "s"})` : "";
  return `${base}${subs} in ${label}`;
}
