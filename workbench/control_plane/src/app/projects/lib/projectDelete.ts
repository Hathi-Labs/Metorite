/**
 * Projects · the rules behind the delete confirmation (WS-27bg slice 2, H-8).
 *
 * Pure: no React, no DOM, no API client — the same split `projectMenu.ts` and
 * `taskMenu.ts` make. What lives here is the part that is wrong-but-plausible
 * in a way no click can catch: a confirmation that accepts the wrong word, and
 * a sentence that under-reports what a cascade is about to destroy.
 *
 * ## What the dialog may and may not claim
 *
 * `DELETE /projects/nodes/{id}` cascades over the subtree, every task in it and
 * every grant. The counts come from `GET /nodes/{id}/summary`, which is the
 * read the dashboards already use — no second aggregate, and no count taken
 * over rows in the browser (`api.ts` says why).
 *
 * ⚠️ **`NodeSummary.projects` counts descendant PROJECTS and deliberately not
 * folders**, because a folder holds no work. A cascade does not make that
 * distinction — it takes the folders too. So the sentence below names what is
 * COUNTABLE and never claims to be the whole inventory, and the dialog says
 * "and everything under it" beside it. A confirmation that under-counts is
 * worse than one that declines to count: it reads as a complete list.
 */

import type { NodeSummary } from "./api";

/** `1 task` / `4 tasks`, with the number first so the eye lands on it. */
function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * What is about to be destroyed, as clauses a sentence can join.
 *
 * Empty when the node holds nothing countable — the caller then says so in its
 * own words rather than rendering "0 subprojects and 0 tasks", which reads as
 * a warning about nothing and trains people to click through the dialog.
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
