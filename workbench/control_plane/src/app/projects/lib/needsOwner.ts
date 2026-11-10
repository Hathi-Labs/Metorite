/**
 * WS-27bn RA-6 — "Needs an owner" in Open work (`projects_reports.md` §6.8
 * B and F).
 *
 * ⚠️ **The server decides each figure.** `analytics.needs_owner_body` counts
 * the open tasks with no assignee and no open run, names 20 at most, and
 * says whether they lead the open work (`leads`). This file holds the words
 * and the rule, and it orders nothing itself.
 *
 * ⚠️ **No rank of people (§6.8 E1).** The group lists tasks, the soonest due
 * first, as the server sends them. Each row opens its task.
 */
import type { LoadReport, NeedsOwner } from "./api";
import { shortDate } from "./outlook";
import { PANEL_ROWS, type WaitingLine, listRows, taskLabel } from "./waiting";

export const NEEDS_OWNER_TITLE = "Needs an owner";

/** The rule of the group, for its `InfoTip` (§5 "Needs an owner"). */
export const NEEDS_OWNER_RULE =
  "An open task with no assignee and no open agent run. A task with an open" +
  " run has an owner, also when that run is quiet. Needs attention shows a" +
  " quiet run.";

/** The group, or `null` when the server sent none or none needs an owner. */
export function needsOwnerOf(data: LoadReport | null | undefined): NeedsOwner | null {
  const owner = data?.needs_owner;
  if (!owner || typeof owner !== "object") return null;
  if (typeof owner.total !== "number" || owner.total <= 0) return null;
  return owner;
}

/** "Due 12 Mar 2027", or "No due date". */
function dueWords(iso: string | null | undefined): string {
  return iso ? `Due ${shortDate(iso)}` : "No due date";
}

/**
 * The rows the panel shows, and how many more the server counted. The
 * panel shows `PANEL_ROWS`, and the table under it names every row sent.
 */
export function needsOwnerRows(owner: NeedsOwner): { shown: WaitingLine[]; more: number } {
  const lines = (Array.isArray(owner.tasks) ? owner.tasks : []).map((t) => ({
    key: t.id,
    id: t.id,
    label: taskLabel({ task_number: t.task_number ?? null, title: t.title }),
    detail: dueWords(t.due_at),
  }));
  return listRows(lines, owner.total, PANEL_ROWS);
}

/** "25 of 26 open tasks have no owner." The count line of the group. */
export function needsOwnerLine(owner: NeedsOwner, totalTasks: number | undefined): string {
  const n = owner.total;
  const has = n === 1 ? "has" : "have";
  return typeof totalTasks === "number" && totalTasks >= n
    ? `${n} of ${totalTasks} open ${totalTasks === 1 ? "task" : "tasks"} ${has} no owner.`
    : `${n} open ${n === 1 ? "task" : "tasks"} ${has} no owner.`;
}
