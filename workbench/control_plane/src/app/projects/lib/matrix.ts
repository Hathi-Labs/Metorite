/**
 * Projects · the priority matrix on a task row (D78, owner decision 2026-09-24).
 *
 * Projects and My Tasks show ONE priority system: the matrix of
 * `tasks/lib/priority.ts` (important × urgent × leveraged → seven levels). Its
 * two stated inputs are shared facts about the work, so they live on the task
 * row, and both apps read the same answer:
 *
 *   • Important — `pm_tasks.importance >= IMPORTANT_AT` (2). The column keeps
 *     its 0-3 values so every filter, merge and recurrence reader still works.
 *   • Leveraged — `pm_tasks.leveraged` (migration 218).
 *   • Urgent — derived from `due_at`. Never stored.
 *
 * ⚠️ **One rule, imported.** This file adapts a `TaskRow` to the Tasks app's
 * matrix and computes nothing of its own. A second formula here would be a
 * second priority vocabulary, which is the defect D78 removes (CLAUDE.md §4).
 */
import {
  CELL_META,
  CELLS_IN_ORDER,
  importanceFor,
  importantFromImportance,
  priorityCell,
  type PriorityCell,
} from "@/app/tasks/lib/priority";
import type { TaskRow } from "./api";

/** The matrix's inputs, as the Tasks app's controls expect them. */
export function matrixOf(
  task: Pick<TaskRow, "importance" | "leveraged" | "due_at">,
): { important: boolean | undefined; leveraged: boolean; dueAt: string | undefined } {
  return {
    important: importantFromImportance(task.importance),
    leveraged: Boolean(task.leveraged),
    dueAt: task.due_at ?? undefined,
  };
}

/** The task's level. `now` is injectable for tests. */
export function taskCell(
  task: Pick<TaskRow, "importance" | "leveraged" | "due_at">,
  now: number = Date.now(),
): PriorityCell {
  return priorityCell(matrixOf(task), undefined, now);
}

/** The level's label, for a group heading, a chip or an export cell. */
export function cellLabel(cell: PriorityCell): string {
  return CELL_META[cell].label;
}

/** The levels in rank order, 1 (act first) to 7. Grouping uses this order. */
export const MATRIX_LEVELS: readonly PriorityCell[] = CELLS_IN_ORDER;

/**
 * The two stated inputs as ONE choice, for a control with no room for two
 * toggles: the table cell's editor and the bulk bar. Urgent is not here,
 * because the due date sets it.
 */
export type MatrixFlags = "" | "important" | "leveraged" | "both";

export const MATRIX_FLAG_OPTIONS: readonly { value: MatrixFlags; label: string }[] = [
  { value: "", label: "Not flagged" },
  { value: "important", label: "Important" },
  { value: "leveraged", label: "Leveraged" },
  { value: "both", label: "Important and leveraged" },
];

/** A row's current choice. An unjudged task (importance NULL) reads "". */
export function flagsOf(task: Pick<TaskRow, "importance" | "leveraged">): MatrixFlags {
  const important = Boolean(importantFromImportance(task.importance));
  const leveraged = Boolean(task.leveraged);
  if (important && leveraged) return "both";
  if (important) return "important";
  if (leveraged) return "leveraged";
  return "";
}

/**
 * A choice → the task PATCH.
 *
 * With no `current` (a new task), both fields go. With the task's current
 * row, only what CHANGED goes, and an Important task that stays Important
 * keeps its stored value: a 3 from before D78 stays 3, because filters,
 * merge and the chat tools still read the number. Review, 2026-09-24.
 */
export function flagsPatch(
  flags: MatrixFlags,
  current?: Pick<TaskRow, "importance" | "leveraged">,
): { importance?: number; leveraged?: boolean } {
  const important = flags === "important" || flags === "both";
  const leveraged = flags === "leveraged" || flags === "both";
  if (!current) return { importance: importanceFor(important), leveraged };
  const out: { importance?: number; leveraged?: boolean } = {};
  if (important !== Boolean(importantFromImportance(current.importance))) {
    out.importance = importanceFor(important);
  }
  if (leveraged !== Boolean(current.leveraged)) out.leveraged = leveraged;
  return out;
}

/**
 * The bulk bar's priority actions. Each sets ONE flag and leaves the other
 * alone: a selection is mixed, and "Leveraged" must not clear Important on
 * every task that had it (review, 2026-09-24).
 */
export type BulkFlagAction = "important:on" | "important:off" | "leveraged:on" | "leveraged:off";

export const BULK_FLAG_OPTIONS: readonly { value: BulkFlagAction; label: string }[] = [
  { value: "important:on", label: "Mark important" },
  { value: "important:off", label: "Clear important" },
  { value: "leveraged:on", label: "Mark leveraged" },
  { value: "leveraged:off", label: "Clear leveraged" },
];

export function bulkFlagPatch(action: BulkFlagAction): { importance?: number; leveraged?: boolean } {
  const on = action.endsWith(":on");
  return action.startsWith("important")
    ? { importance: importanceFor(on) }
    : { leveraged: on };
}

/**
 * The flags a level IMPLIES, for a quick-add inside that level's group.
 *
 * Urgent is the due date's to set, so the three urgent levels imply only
 * their flags: a task added under "Urgent" is Important, and it reads Urgent
 * once it has a close due date. A drop onto a level is refused instead
 * (`dropRefusal`), because a drop moves an existing task and would have to
 * land it somewhere other than where it was dropped.
 */
export function flagsForCell(cell: string): MatrixFlags {
  switch (cell) {
    case "critical":
    case "high-leverage":
      return "both";
    case "urgent":
    case "important":
      return "important";
    case "quick-leverage":
    case "speculative-bet":
      return "leveraged";
    default:
      return "";
  }
}
