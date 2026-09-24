// Waiting-For math — the pure predicates behind the "who / what / since-when"
// list (spec: project-docs/specs/task_manager_app.md §1 line 46, §6).
// No React, no store: just Date arithmetic over an already-loaded MyTask, so
// the view and its unit tests read the same rules. Same shape as
// lib/ordering.ts and lib/scheduling.ts.
//
// Two DIFFERENT clocks live here and must not be confused:
//   overdue = past the line the item is judged on (see isWaitingOverdue)
//   stale   = long since `delegatedAt` — nobody has heard anything in a while
// An item can be either, both, or neither.
//
// `expectedBy` is an EXPLICIT PROMISE, and only that. NULL is not "no
// deadline", it is "nobody promised a date" — in which case the honest line to
// judge against is the item's own `dueAt`, read live. The server therefore
// stores NULL at every delegate/capture/sync site and never derives a copy of
// `dueAt` into it; a value in `expectedBy` means a human stated it (via
// PATCH /tasks/items/{id}) and it stands on its own, deliberately independent
// of `dueAt`. That is what keeps the badge from going stale: the common case
// has nothing to go stale, and the uncommon case is a fact somebody asserted.

import { MyTask } from "./types";

/** Days of silence after which a waiting-for is "stale".
 *
 *  ⚠️ CONTRACT: this is the client half of a rule the server also states, in
 *  `GET /tasks/insights` (`apps/services/gateway/gateway/routes/tasks/ai.py`,
 *  `inbox_insights`: `w.delegated_at < now() - interval '5 days'`). The banner
 *  and `insights.stale_waiting` must never disagree about the same list, so if
 *  one side moves the other moves in the same change. */
export const STALE_WAITING_DAYS = 5;

const DAY_MS = 86_400_000;

/** ms elapsed since an ISO timestamp, or null when there isn't one / it's
 *  unparseable. Kept private: every caller below wants the null-safety. */
function elapsed(iso: string | undefined, nowMs: number): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  return Number.isNaN(then) ? null : nowMs - then;
}

/** Whole days the item has been on the Waiting-For list — the "since-when"
 *  column (§1 line 46). Null when it was never delegated (no `delegatedAt`),
 *  which is the only honest answer: the view shows nothing rather than "0d".
 *  Floors, so it reads "3d" for anything from 3d 0h to 3d 23h. Negative
 *  elapsed (a delegation dated in the future) clamps to 0. */
export function daysWaiting(
  item: Pick<MyTask, "delegatedAt">,
  nowMs: number = Date.now(),
): number | null {
  const ms = elapsed(item.delegatedAt, nowMs);
  if (ms === null) return null;
  return Math.max(0, Math.floor(ms / DAY_MS));
}

/** True iff the date this waiting-for is judged on has passed (spec §6 line
 *  540: "flags waiting-for rows past `expected_by`").
 *
 *  The line is `expectedBy ?? dueAt`, and the two are different facts:
 *    • `expectedBy` set — someone actually promised this date. It wins, in
 *      BOTH directions: a promise for next week keeps an item that is already
 *      past my own deadline off the overdue list, and a promise for yesterday
 *      flags an item whose deadline is still ahead.
 *    • `expectedBy` null — nobody promised anything, so the only honest line
 *      is my own deadline for the work, read live off `dueAt`. Nothing is
 *      copied, so nothing can go stale when that deadline moves.
 *  Neither present ⇒ false: there is no date to be late against. */
export function isWaitingOverdue(
  item: Pick<MyTask, "expectedBy" | "dueAt">,
  nowMs: number = Date.now(),
): boolean {
  const line = item.expectedBy || item.dueAt;
  if (!line) return false;
  const due = new Date(line).getTime();
  return !Number.isNaN(due) && due < nowMs;
}

/** Which fact `isWaitingOverdue` judged against — so the view can SAY which
 *  one it is ("promised by …" reads differently from "due …") instead of
 *  showing a bare date that means two things. Null when there is no line. */
export function waitingLine(
  item: Pick<MyTask, "expectedBy" | "dueAt">,
): { iso: string; kind: "promised" | "due" } | null {
  if (item.expectedBy) return { iso: item.expectedBy, kind: "promised" };
  if (item.dueAt) return { iso: item.dueAt, kind: "due" };
  return null;
}

/** True iff more than STALE_WAITING_DAYS have passed since delegation — the
 *  "silently rotting" case §6 exists to prevent. Strictly greater, matching
 *  the server's `delegated_at < now() - interval '5 days'`: at exactly five
 *  days it is not yet stale. */
export function isStaleWaiting(
  item: Pick<MyTask, "delegatedAt">,
  nowMs: number = Date.now(),
): boolean {
  const ms = elapsed(item.delegatedAt, nowMs);
  return ms !== null && ms > STALE_WAITING_DAYS * DAY_MS;
}

/** One person's Waiting-For pile. `key` is stable for React and for tests. */
export interface WaitingGroup {
  key: string;
  /** who we're waiting on ("Unassigned" when the record has no person). */
  label: string;
  items: MyTask[];
  overdueCount: number;
}

const UNASSIGNED = "Unassigned";

/** Group a waiting list by WHO we're waiting on — the axis the view is built
 *  around, because the human action ("chase Sai") is per-person, not per-task.
 *  Within a group the longest-waiting item comes first (that's the one at risk
 *  of rotting); groups are ordered by overdue count, then by size, then name,
 *  so the person you most need to chase is at the top. Pure + deterministic. */
export function groupByWaitingOn(
  items: MyTask[],
  nowMs: number = Date.now(),
): WaitingGroup[] {
  const byKey = new Map<string, WaitingGroup>();
  for (const item of items) {
    // Identity: email when we have one (two "Priya"s are two people), else the
    // display name, else the shared Unassigned bucket.
    const person = item.waitingOn;
    const key =
      person?.email?.toLowerCase() || person?.name?.trim() || UNASSIGNED;
    const label = person?.name?.trim() || person?.email || UNASSIGNED;
    let group = byKey.get(key);
    if (!group) {
      group = { key, label, items: [], overdueCount: 0 };
      byKey.set(key, group);
    }
    group.items.push(item);
    if (isWaitingOverdue(item, nowMs)) group.overdueCount += 1;
  }
  const groups = [...byKey.values()];
  for (const g of groups) {
    g.items.sort(
      (a, b) =>
        (daysWaiting(b, nowMs) ?? -1) - (daysWaiting(a, nowMs) ?? -1) ||
        a.title.localeCompare(b.title),
    );
  }
  return groups.sort(
    (a, b) =>
      b.overdueCount - a.overdueCount ||
      b.items.length - a.items.length ||
      a.label.localeCompare(b.label),
  );
}

/**
 * What a nudge actually achieved, in the one sentence the row shows.
 *
 * 🔴 **`notified` can be empty on a 200, and that is the whole reason this
 * exists.** `POST /tasks/{id}/nudge` delivers only to people who can open the
 * task, and it reports the rest in `skipped` rather than pretending. A button
 * that said "Nudged" either way would leave a member waiting on somebody who
 * was never told — the same shape as a board that claims to hold every task
 * and holds one page.
 *
 * It lives here rather than in `WaitingForView` because `vitest.config.ts` is
 * `environment: "node"` and never collects a `.tsx` (D-PM-21), so a rule
 * written in the component would carry no fence.
 */
export function nudgeOutcome(result: {
  notified: readonly string[];
  skipped: readonly string[];
}): { ok: boolean; message: string } {
  const notified = result.notified ?? [];
  const skipped = result.skipped ?? [];
  if (notified.length === 1) {
    return { ok: true, message: `Nudged ${notified[0]}.` };
  }
  if (notified.length > 1) {
    return { ok: true, message: `Nudged ${notified.length} people.` };
  }
  if (skipped.length === 1) {
    return {
      ok: false,
      message: `${skipped[0]} cannot open this task, so nothing was sent.`,
    };
  }
  if (skipped.length > 1) {
    return {
      ok: false,
      message: `${skipped.length} people cannot open this task, so nothing was sent.`,
    };
  }
  return { ok: false, message: "Nobody was nudged." };
}
