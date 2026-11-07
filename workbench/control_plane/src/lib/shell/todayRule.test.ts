/**
 * The fence of the client half of the one Today rule (WS-MDC CC-2,
 * `my_day_command_centre.md` §10).
 *
 * `tests/fixtures/today_rule_cases.json` is the contract. The pytest
 * (`tests/unit/test_today_rule_r8.py`) runs the same cases through the SQL
 * predicate on Postgres and through `mine.in_today`. This file runs them
 * through `todayWhy` and `isInToday`. So the client check and the server
 * predicate cannot disagree on a case of the file.
 *
 * Mutations, run by hand on 2026-11-07 (each turned this file red, then was
 * restored):
 * - `todayWhy` with no `isOpenForToday` check: the closed and trash cases fail.
 * - the `scheduled` arm read in UTC, not in the zone: "my block starts at my
 *   midnight of today" fails.
 * - `inTodayChip` with no `serverIds` branch: the server-set case fails.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { isInToday } from "@/app/tasks/lib/today";

import {
  TODAY_WHY_ORDER,
  type TodayRuleFields,
  type TodayWhy,
  inTodayChip,
  inTodaySet,
  toFit,
  todayWhy,
} from "./todayRule";

interface FixtureCase {
  name: string;
  task: {
    stated: string | null;
    effective: string;
    category: string;
    assignee: "me" | "other" | null;
    completed: boolean;
    defer_until: string | null;
    start_date: string | null;
    due_at: string | null;
  };
  mine: {
    focus_on: string | null;
    scheduled_start: string | null;
    scheduled_end: string | null;
    one_thing: boolean;
  };
  why: TodayWhy[];
}

const FIXTURE = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../../../tests/fixtures/today_rule_cases.json", import.meta.url)),
    "utf8",
  ),
) as { zone: string; today: string; at: string; cases: FixtureCase[] };

/** One case as the client holds the row: the effective disposition, as the lens sends it. */
function rowOf(c: FixtureCase, id: string): TodayRuleFields {
  return {
    id,
    disposition: c.task.effective as TodayRuleFields["disposition"],
    isMine: c.task.assignee === "me",
    completedAt: c.task.completed ? FIXTURE.at : undefined,
    archivedAt: undefined,
    dueAt: c.task.due_at ?? undefined,
    startDate: c.task.start_date ?? undefined,
    deferUntil: c.task.defer_until ?? undefined,
    focusOn: c.mine.focus_on,
    scheduledStart: c.mine.scheduled_start,
  };
}

const ctxOf = (c: FixtureCase, id: string) => ({
  today: FIXTURE.today,
  timeZone: FIXTURE.zone,
  oneThingId: c.mine.one_thing ? id : "another-task",
});

describe("the one Today rule, on the shared fixture", () => {
  it("reads every case, and each why keeps the order of the arms", () => {
    expect(FIXTURE.cases.length).toBeGreaterThanOrEqual(20);
    for (const c of FIXTURE.cases) {
      const order = c.why.map((w) => TODAY_WHY_ORDER.indexOf(w));
      expect(order).toEqual([...order].sort((a, b) => a - b));
    }
  });

  it("covers each arm alone and each way out", () => {
    const alone = new Set(FIXTURE.cases.filter((c) => c.why.length === 1).map((c) => c.why[0]));
    expect([...alone].sort()).toEqual(["due", "focus", "one_thing", "scheduled"]);
    expect(FIXTURE.cases.some((c) => c.why.length === 0)).toBe(true);
  });

  it.each(FIXTURE.cases.map((c, i) => [c.name, c, `task-${i}`] as const))("%s", (_name, c, id) => {
    const row = rowOf(c, id);
    const ctx = ctxOf(c, id);
    expect(todayWhy(row, ctx)).toEqual(c.why);
    expect(inTodaySet(row, ctx)).toBe(c.why.length > 0);
    // The `due` arm alone is the old rule, `isInToday`.
    expect(isInToday(row, FIXTURE.today, FIXTURE.zone)).toBe(c.why.includes("due"));
  });
});

describe("the To fit tray", () => {
  it("is the set minus the tasks with a block today", () => {
    const rows = FIXTURE.cases.map((c, i) => ({ c, row: rowOf(c, `task-${i}`) }));
    const ctx = { today: FIXTURE.today, timeZone: FIXTURE.zone, oneThingId: null };
    const fit = toFit(
      rows.map((r) => r.row),
      ctx,
    ).map((r) => r.id);
    const want = rows
      .filter(({ row }) => {
        const why = todayWhy(row, ctx);
        return why.length > 0 && !why.includes("scheduled");
      })
      .map(({ row }) => row.id);
    expect(fit).toEqual(want);
    expect(fit.length).toBeGreaterThan(0);
    for (const { c, row } of rows) {
      if (c.why.includes("scheduled")) expect(fit).not.toContain(row.id);
    }
  });
});

describe("the Today chip of My work", () => {
  const due = FIXTURE.cases.find((c) => c.why.length === 1 && c.why[0] === "due")!;
  const none = FIXTURE.cases.find((c) => c.why.length === 0 && c.task.effective === "NEXT")!;
  const ctx = { today: FIXTURE.today, timeZone: FIXTURE.zone };

  it("keeps the old rule when the server sends no ids (the flag off)", () => {
    expect(inTodayChip(rowOf(due, "a"), ctx, null)).toBe(true);
    expect(inTodayChip(rowOf(none, "b"), ctx, null)).toBe(false);
    expect(inTodayChip(rowOf(none, "b"), ctx, undefined)).toBe(false);
  });

  it("lists the server set when the server sends ids (the flag on)", () => {
    const ids = new Set(["b"]);
    expect(inTodayChip(rowOf(none, "b"), ctx, ids)).toBe(true);
    // The local check still holds a row that the next read will send.
    expect(inTodayChip(rowOf(due, "a"), ctx, ids)).toBe(true);
    // A row the member closed since the read leaves the chip at once.
    const closed = { ...rowOf(none, "b"), completedAt: FIXTURE.at };
    expect(inTodayChip(closed, ctx, ids)).toBe(false);
  });
});
