/**
 * Projects · the timeline adapter (STU-D6).
 *
 * Maps a Projects task to the row shape of the timeline block
 * (`@/components/timeline/types`), and back. The block draws rows. It never
 * reads a task. This file is the one place that knows both shapes.
 *
 * What stays a Projects rule, and so lives here and not in the block:
 * which date fields a task has, that a parent with no dates takes the span of
 * its subtasks (D-PM-11), and that reversed dates show as the span they imply.
 */

import { fromDayKey, dayKey } from "@/components/timeline/days";
import { orderedSpan } from "@/components/timeline/drag";
import { unionSpan } from "@/components/timeline/scale";
import type { TimelineRow as BlockRow } from "@/components/timeline/types";

import type { TaskRow } from "./api";

/** A task's own scheduled interval, or `null` when it has no dates. */
export function interval(task: TaskRow): { from: string; to: string } | null {
  const start = task.start_date ? task.start_date.slice(0, 10) : null;
  const due = task.due_at ? dayKey(new Date(task.due_at)) : null;
  if (!start && !due) return null;
  const a = start ?? (due as string);
  const b = due ?? (start as string);
  // Bad data — due before start — is shown as the span it implies rather than
  // dropped: the timeline is the one view that would have made it obvious.
  return orderedSpan(a, b);
}

/**
 * The interval a ROW occupies, folding in its children.
 *
 * A parent with no dates of its own still gets a bar when its subtasks have
 * them. `derived` marks it so the UI can say the dates were not typed here.
 */
export function rowInterval(
  task: TaskRow,
  children: readonly TaskRow[],
): { from: string; to: string; derived: boolean } | null {
  const own = interval(task);
  if (own) return { ...own, derived: false };
  const all = unionSpan(
    children.map(interval).filter(Boolean) as { from: string; to: string }[],
  );
  return all ? { ...all, derived: true } : null;
}

/** A task, with its subtasks folded in, as one row of the block. */
export function taskToBlockRow(
  task: TaskRow,
  children: readonly TaskRow[] = [],
): BlockRow<TaskRow> {
  const span = rowInterval(task, children);
  return {
    id: task.id,
    label: task.title,
    start: span ? fromDayKey(span.from) : null,
    end: span ? fromDayKey(span.to) : null,
    // The bar tone is drawn by the view from the status. The block gets none.
    tone: null,
    derived: span?.derived ?? false,
    data: task,
  };
}

/** The task a block row came from. */
export function blockRowTask(row: BlockRow<TaskRow>): TaskRow {
  return row.data;
}
