/**
 * Projects · the Archive view of My tasks (ARC-1, `one_projects_app.md` §9b).
 *
 * Owner, 2026-11-03: archived tasks must "show up appropriately in the
 * archive section". Nothing archives by itself (D170.4), so this view lists
 * what the member filed, across projects, and puts it back.
 *
 * ⚠️ **One "mine", one list route.** The rows come from `GET /projects/tasks`
 * with `scope=mine` and `archived_only=true`. That read applies the one
 * definition of "mine" (`core.mine_clause`) inside the member's grants. This
 * module adds no second definition and no second route.
 *
 * ⚠️ **A task in an archived project cannot come back alone.** Restoring it
 * would leave it out of every list, because its project is filed. So its row
 * names the project and its Restore is off, with the words below.
 */

import type { TaskRow } from "./api";
import { myTasksQuery } from "./myTasks";

/** Why Restore is off on a row whose project is archived. */
export const ARCHIVED_PROJECT_REASON = "Its project is archived. Restore the project first.";

/** The label of the chip and the view. */
export const ARCHIVE_LABEL = "Archive";

/** What the empty view is for. */
export const ARCHIVE_EMPTY_TEXT = "Nothing is archived.";
export const ARCHIVE_EMPTY_HINT =
  "Tasks you archive wait here. Nothing archives by itself, and you can restore any of them.";

/** What a failed read says, and what to do next. */
export const ARCHIVE_READ_FAILED = "The archive could not be loaded just now.";

/** One page of the archive read: the one list route over mine, filed tasks only. */
export function archiveQuery(page: number): Record<string, string | number> {
  return myTasksQuery({ archived_only: "true" }, page);
}

export interface ArchiveRow {
  task: TaskRow;
  /** The names of the project's path, for the project chip. */
  path: readonly string[];
  /** True when the task's project (or a parent of it) is archived. */
  projectArchived: boolean;
  /** Restore works on this row. */
  canRestore: boolean;
}

function stamp(task: TaskRow): number {
  const at = task.archived_at ? Date.parse(task.archived_at) : NaN;
  return Number.isFinite(at) ? at : 0;
}

/**
 * Every row the server sent, newest filed first. Nothing is dropped: a row in
 * an archived project stays in the list, with Restore off.
 */
export function archiveRows(
  tasks: readonly TaskRow[],
  pathOf: (projectId: string) => readonly string[],
  projectArchived: (projectId: string) => boolean,
): ArchiveRow[] {
  return tasks
    .filter((task) => Boolean(task.archived_at))
    .map((task) => {
      const filed = projectArchived(task.project_id);
      return { task, path: pathOf(task.project_id), projectArchived: filed, canRestore: !filed };
    })
    .sort((a, b) => stamp(b.task) - stamp(a.task));
}

/** The ids that Select all takes: the rows that can come back. */
export function restorableIds(rows: readonly ArchiveRow[]): string[] {
  return rows.filter((row) => row.canRestore).map((row) => row.task.id);
}

/** Selected ids that still show a restorable row. A reload may remove some. */
export function pruneSelection(selected: ReadonlySet<string>, rows: readonly ArchiveRow[]): Set<string> {
  const keep = new Set(restorableIds(rows));
  return new Set([...selected].filter((id) => keep.has(id)));
}

/** One task uses the unarchive route, many use the bulk route. */
export type RestorePlan = { kind: "one"; id: string } | { kind: "many"; ids: string[] };

export function restorePlan(ids: readonly string[]): RestorePlan | null {
  if (ids.length === 0) return null;
  if (ids.length === 1) return { kind: "one", id: ids[0] };
  return { kind: "many", ids: [...ids] };
}

/** The body of the bulk request that restores many tasks. */
export function restoreBody(ids: readonly string[]): { task_ids: string[]; action: "unarchive" } {
  return { task_ids: [...ids], action: "unarchive" };
}

/** The toast title after a restore. */
export function restoreReceipt(count: number): string {
  return count === 1 ? "Task restored from the archive" : `${count} tasks restored from the archive`;
}

/** The words of a restore that did not land for every task. */
export function restoreShortfall(requested: number, restored: number): string | null {
  const left = requested - restored;
  if (left <= 0) return null;
  return left === 1
    ? "1 task could not be restored. It may have changed. Reload the archive and try again."
    : `${left} tasks could not be restored. They may have changed. Reload the archive and try again.`;
}

/** The ids a bulk reply says it restored. */
export function restoredIds(reply: {
  results?: ReadonlyArray<{ task_id: string }>;
}): string[] {
  return (reply.results ?? []).map((r) => r.task_id);
}
