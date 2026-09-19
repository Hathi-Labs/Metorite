/**
 * Projects · the rules behind the delete confirmation (WS-27bg slice 2, H-8).
 *
 * Pure: no React, no DOM, no API client — the same split `projectMenu.ts` and
 * `taskMenu.ts` make. What lives here is the part that is wrong-but-plausible
 * in a way no click can catch: a confirmation that accepts the wrong word, and
 * a sentence that under-reports what a cascade is about to destroy.
 *
 * ## 🔴 The dialog may NEVER claim that a node is empty
 *
 * This is the rule the whole module exists for, and the first version of it
 * broke the rule in the one branch nobody reads twice.
 *
 * `DELETE /projects/nodes/{id}` cascades over the subtree, every task in it and
 * every grant. The counts come from `GET /nodes/{id}/summary`. **The two do not
 * count the same rows, and the gap is ordinary history rather than an edge
 * case:**
 *
 * 1. **Archived tasks.** `node_counts_sql` (`tree.py`) filters
 *    `t.archived_at IS NULL`. `delete_node` counts `pm_tasks` with no such
 *    filter. A project under a lifecycle policy accumulates archived tasks for
 *    years, so a two-year-old project can summarise as 0 tasks and delete 500.
 * 2. **Folders.** `NodeSummary.projects` counts descendants whose kind is
 *    `project`. A space holding three folders reports 0. The cascade takes the
 *    folders.
 *
 * So the counts are a FLOOR, never an inventory. The dialog states the removal
 * unconditionally and offers the counts as a lower bound beside it. It must
 * not print "holds no subprojects and no tasks", which is a positive claim of
 * emptiness that the server is free to contradict a second later — and which
 * is exactly what a member skims past on the way to the button.
 */

import type { NodeSummary } from "./api";

/** `1 task` / `4 tasks`, with the number first so the eye lands on it. */
function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * The LIVE, VISIBLE work under this node, as clauses a sentence can join.
 *
 * ⚠️ A floor, not an inventory — see the module header. Empty means only that
 * the summary counted nothing, which is NOT the same as the node being empty,
 * and no caller may render it as such.
 */
export function deletionClauses(summary: NodeSummary | null): string[] {
  if (!summary) return [];
  const out: string[] = [];
  if (summary.projects > 0) {
    out.push(plural(summary.projects, "subproject", "subprojects"));
  }
  if (summary.tasks > 0) out.push(plural(summary.tasks, "task", "tasks"));
  return out;
}

/**
 * The clauses as one English list: `a`, `a and b`, `a, b and c`.
 *
 * Separate from `deletionClauses` so a caller that wants to draw the counts as
 * chips rather than prose is not forced through the sentence.
 */
export function joinClauses(clauses: readonly string[]): string {
  if (clauses.length === 0) return "";
  if (clauses.length === 1) return clauses[0];
  return `${clauses.slice(0, -1).join(", ")} and ${clauses[clauses.length - 1]}`;
}

/**
 * Has the member typed this project's name back?
 *
 * **Trimmed and case-insensitive, on purpose.** The friction exists to make
 * somebody read the name of the thing they are about to destroy, not to test
 * their shift key. An exact-match gate fails a correct answer on a project
 * called `Q3 Roadmap` typed as `q3 roadmap`, and the failure teaches nothing —
 * the person has already proved they read it.
 *
 * ⚠️ **An empty name never confirms.** `pm_projects.name` is not nullable, so
 * this should be unreachable; it is guarded anyway, because the reachable
 * version of the bug is a dialog rendered before its row has loaded, where
 * `name` is `""` and an untouched field would confirm the delete by itself.
 */
export function confirmsDeletion(typed: string, name: string): boolean {
  const target = name.trim().toLocaleLowerCase();
  if (target === "") return false;
  return typed.trim().toLocaleLowerCase() === target;
}

/**
 * The caveat that must accompany any count this module produces.
 *
 * Exported as a constant rather than written into the dialog so that a second
 * surface showing these counts cannot show them without it.
 */
export const COUNT_CAVEAT =
  "Counts cover live work you can see. Archived tasks and folders are not counted, and are deleted too.";
