/**
 * WS-27bn R3b — the Rebalance panel's words, and nothing else.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R3b.
 *
 * ⚠️ **No arithmetic lives here, and none may.** Every task, helper, count
 * and date arrives finished from the rebalance route's body
 * (`analytics_rebalance.py` `rebalance_body`), which the report section
 * copies. This file chooses words and reads the body defensively, as
 * `conflicts.ts` does. `rebalance.test.ts` scans it for arithmetic.
 *
 * ⚠️ **The lists are ABSENT for a reader without `admin:members:read`, not
 * empty.** A helper list names who holds which skill. `hr_visible` says which
 * answer arrived, and the panel says so in one line.
 *
 * ⚠️ **Read only.** There is no Assign, no Dismiss and no task link here.
 * Slice R4b adds them.
 */
import { asList } from "./analyticsRead";
import type { RebalancePickup, RebalanceReport, RebalanceTask } from "./api";
import { shortDate } from "./outlook";

/** How many helpers one task names. The route sends three at most. */
export const HELPERS_SHOWN = 3;

/** The line the panel, the email and the chat card print without the grant. */
export const REBALANCE_HR_HINT =
  "Rebalancing needs HR read access. An admin can see it.";

/** The at-risk tasks the server sent, in its order. */
export function rebalanceTasks(data: RebalanceReport | null | undefined): RebalanceTask[] {
  return asList<RebalanceTask>(data?.at_risk).filter(
    (t): t is RebalanceTask =>
      !!t && typeof t === "object" && typeof t.title === "string"
  );
}

/** The idle people the server sent, in its order. */
export function rebalancePickups(
  data: RebalanceReport | null | undefined
): RebalancePickup[] {
  return asList<RebalancePickup>(data?.pickups).filter(
    (p): p is RebalancePickup =>
      !!p && typeof p === "object" && typeof p.name === "string"
  );
}

/** "Held by Hal · due 27 Sep 2026". A task with no due date says so. */
export function holderLine(task: RebalanceTask): string {
  const who = task.holder?.name || task.holder?.email || "somebody";
  return `Held by ${who} · ${task.due_on ? `due ${shortDate(task.due_on)}` : "no due date"}`;
}

/** The first three helpers' names, or null when the route found none. */
export function helpersLine(task: RebalanceTask): string | null {
  const names = asList<{ name?: string }>(task.candidates)
    .slice(0, HELPERS_SHOWN)
    .map((c) => c?.name)
    .filter((n): n is string => typeof n === "string" && n.length > 0);
  return names.length ? names.join(", ") : null;
}

/** "t1, t2": the titles an idle person could take, in the server's order. */
export function pickupLine(person: RebalancePickup): string {
  return asList<{ title?: string }>(person.tasks)
    .map((t) => t?.title)
    .filter((t): t is string => typeof t === "string" && t.length > 0)
    .join(", ");
}

/**
 * What the caps cut, in words, or null when nothing was cut. The totals are
 * the server's: `at_risk_total` before the join's cap of eight, and
 * `pickups_total` before the report's cap of twenty.
 */
export function rebalanceCapNote(
  data: RebalanceReport | null | undefined,
  shownTasks: number,
  shownPeople: number
): string | null {
  const parts: string[] = [];
  const tasks = data?.at_risk_total;
  if (typeof tasks === "number" && shownTasks < tasks) {
    parts.push(`Showing ${shownTasks} of ${tasks} tasks at risk.`);
  }
  const people = data?.pickups_total;
  if (typeof people === "number" && shownPeople < people) {
    parts.push(`Showing ${shownPeople} of ${people} people who could take work.`);
  }
  return parts.length ? parts.join(" ") : null;
}
