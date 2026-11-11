/**
 * Shell · the one Today rule, on the client (WS-MDC CC-2,
 * `my_day_command_centre.md` §9.2, D-MD-2).
 *
 * The server owns the rule. `gateway/routes/projects/today_rule.py` holds ONE
 * SQL predicate, and `GET /projects/my/today?with_tasks=true` answers its set.
 * This module is the optimistic local check of the same four arms, so a row
 * that the member just marked or blocked can show before the next read. It is
 * never the rule of record.
 *
 * A task is in Today when it is open and one of these holds:
 *
 * 1. `one_thing`: it is the day's One Thing.
 * 2. `focus`: my mark "today's focus" equals my today.
 * 3. `scheduled`: my block starts in my local day.
 * 4. `due`: `isInToday` (`app/tasks/lib/today.ts`), the old rule of due and
 *    start dates. It stays the one copy of that arm.
 *
 * The Calendar's "To fit" tray is the set minus the tasks with a block today
 * (`toFit`). `tests/fixtures/today_rule_cases.json` holds the cases, and the
 * pytest of the predicate and `todayRule.test.ts` both read it.
 */

import { isInToday } from "@/app/tasks/lib/today";
import { localDate } from "@/app/tasks/lib/utils";
import type { MyTask } from "@/app/tasks/lib/types";

/** Why a task is in Today, in the order a row lists it. */
export type TodayWhy = "one_thing" | "focus" | "scheduled" | "due";

export const TODAY_WHY_ORDER: readonly TodayWhy[] = ["one_thing", "focus", "scheduled", "due"];

/** The facts the four arms read off a task. `disposition` is the EFFECTIVE one. */
export type TodayRuleFields = Pick<
  MyTask,
  "id" | "disposition" | "isMine" | "archivedAt" | "completedAt" | "dueAt" | "startDate" | "deferUntil"
> & {
  /** My mark, "YYYY-MM-DD", or none. */
  focusOn?: string | null;
  /** The start of my block, an ISO instant, or none. */
  scheduledStart?: string | null;
};

/** The member's day: the date, the zone, and the One Thing of the day. */
export interface TodayContext {
  today: string;
  timeZone?: string;
  oneThingId?: string | null;
}

/** Open: not archived, not completed, not done and not in my trash. */
export function isOpenForToday(item: Pick<TodayRuleFields, "disposition" | "archivedAt" | "completedAt">): boolean {
  if (item.archivedAt || item.completedAt) return false;
  return item.disposition !== "DONE" && item.disposition !== "TRASH";
}

/** The local day of an ISO instant, or null for a value that does not parse. */
function dayOfInstant(iso: string, timeZone?: string): string | null {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : localDate(ms, timeZone);
}

/** The arms that hold for one task. An empty list is a task outside Today. */
export function todayWhy(item: TodayRuleFields, ctx: TodayContext): TodayWhy[] {
  if (!isOpenForToday(item)) return [];
  const why: TodayWhy[] = [];
  if (ctx.oneThingId && item.id === ctx.oneThingId) why.push("one_thing");
  if (item.focusOn && item.focusOn.slice(0, 10) === ctx.today) why.push("focus");
  if (item.scheduledStart && dayOfInstant(item.scheduledStart, ctx.timeZone) === ctx.today) {
    why.push("scheduled");
  }
  if (isInToday(item, ctx.today, ctx.timeZone)) why.push("due");
  return why;
}

/** Is the task in Today, by the local check? */
export function inTodaySet(item: TodayRuleFields, ctx: TodayContext): boolean {
  return todayWhy(item, ctx).length > 0;
}

/** The "To fit" tray: the rows of the set with no block today, in order. */
export function toFit<T extends TodayRuleFields>(rows: readonly T[], ctx: TodayContext): T[] {
  return rows.filter((row) => {
    const why = todayWhy(row, ctx);
    return why.length > 0 && !why.includes("scheduled");
  });
}

/**
 * Is a row of the Today chip of My work in the chip?
 *
 * `serverIds` is `today_ids` of `GET /projects/my/summary`, which the server
 * sends only with `TODAY_RULE_ORGS` on. With no ids, the chip keeps the old
 * rule, `isInToday`, and nothing changes. With ids, an open row is in the chip
 * when the server set holds it, or when the local check says so before the
 * next read.
 */
export function inTodayChip(
  item: TodayRuleFields,
  ctx: TodayContext,
  serverIds?: ReadonlySet<string> | null,
): boolean {
  if (!serverIds) return isInToday(item, ctx.today, ctx.timeZone);
  if (!isOpenForToday(item)) return false;
  return serverIds.has(item.id) || inTodaySet(item, ctx);
}
