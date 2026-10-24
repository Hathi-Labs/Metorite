/**
 * The Today rule (WS-64 OP-2, `one_projects_app.md` §9 OP-2 item 3).
 * One pure function, so My work in Projects and My Tasks cannot disagree.
 */
import { describe, expect, it } from "vitest";

import { itemsForView, viewCounts } from "./taskStore";
import { isInToday, tasksEntryView, todayRows } from "./today";
import type { MyTask } from "./types";

const TODAY = "2026-10-24";

const task = (id: string, over: Partial<MyTask> = {}): MyTask => ({
  id,
  source: "LOCAL",
  title: `Task ${id}`,
  disposition: "NEXT",
  isMine: true,
  createdAt: "2026-10-01T09:00:00Z",
  updatedAt: "2026-10-01T09:00:00Z",
  ...over,
});

describe("isInToday", () => {
  it("holds an open next action due today", () => {
    expect(isInToday(task("a", { dueAt: "2026-10-24" }), TODAY)).toBe(true);
  });

  it("holds an overdue next action", () => {
    expect(isInToday(task("a", { dueAt: "2026-10-01" }), TODAY)).toBe(true);
  });

  it("holds a next action whose start date has come", () => {
    expect(isInToday(task("a", { startDate: "2026-10-24" }), TODAY)).toBe(true);
    expect(isInToday(task("a", { startDate: "2026-09-30" }), TODAY)).toBe(true);
  });

  it("holds nothing with no date", () => {
    expect(isInToday(task("a"), TODAY)).toBe(false);
  });

  it("holds nothing dated after today", () => {
    expect(isInToday(task("a", { dueAt: "2026-10-25" }), TODAY)).toBe(false);
    // A start date still ahead keeps the task out, even when it is overdue.
    expect(isInToday(task("a", { dueAt: "2026-10-01", startDate: "2026-10-30" }), TODAY)).toBe(false);
  });

  it("holds only a next action", () => {
    for (const disposition of ["INBOX", "WAITING", "SOMEDAY", "REFERENCE", "DONE"] as const) {
      expect(isInToday(task("a", { disposition, dueAt: "2026-10-24" }), TODAY), disposition).toBe(false);
    }
  });

  it("holds only my own task", () => {
    expect(isInToday(task("a", { isMine: false, dueAt: "2026-10-24" }), TODAY)).toBe(false);
  });

  it("leaves out a task that is archived or completed", () => {
    expect(isInToday(task("a", { dueAt: "2026-10-24", archivedAt: "2026-10-20T00:00:00Z" }), TODAY)).toBe(false);
    expect(isInToday(task("a", { dueAt: "2026-10-24", completedAt: "2026-10-20T00:00:00Z" }), TODAY)).toBe(false);
  });

  it("leaves out a task deferred past today", () => {
    expect(isInToday(task("a", { dueAt: "2026-10-24", deferUntil: "2026-10-26T09:00:00Z" }), TODAY, "UTC")).toBe(false);
    expect(isInToday(task("a", { dueAt: "2026-10-24", deferUntil: "2026-10-24T01:00:00Z" }), TODAY, "UTC")).toBe(true);
  });

  it("reads an instant in the member's zone", () => {
    // 23:30 UTC on the 23rd is already the 24th in Kolkata.
    const due = "2026-10-23T23:30:00Z";
    expect(isInToday(task("a", { dueAt: due }), "2026-10-24", "Asia/Kolkata")).toBe(true);
    expect(isInToday(task("a", { dueAt: "2026-10-24T20:00:00Z" }), "2026-10-24", "Asia/Kolkata")).toBe(false);
  });
});

describe("todayRows and the store", () => {
  const ITEMS = [task("due", { dueAt: "2026-10-24" }), task("later", { dueAt: "2026-11-01" }), task("none")];

  it("keeps the order the rows came in", () => {
    expect(todayRows(ITEMS, TODAY).map((i) => i.id)).toEqual(["due"]);
  });

  it("the Today view and the Today count read the same rule", () => {
    expect(itemsForView(ITEMS, "today", null, "all", TODAY).map((i) => i.id)).toEqual(["due"]);
    expect(viewCounts(ITEMS, "all", TODAY).today).toBe(1);
  });

  it("holds no row until the date is known", () => {
    expect(itemsForView(ITEMS, "today", null)).toEqual([]);
    expect(viewCounts(ITEMS).today).toBe(0);
  });
});

describe("the member's zone", () => {
  // 23:30 on 2026-10-24 in Los Angeles (UTC-7) is 06:30 UTC on the 25th.
  // A browser five hours ahead of the member reads that instant as the 25th.
  const due = "2026-10-25T06:30:00Z";

  it("reads an instant in the member's zone and not the browser's", () => {
    const t = task("z", { dueAt: due });
    expect(isInToday(t, "2026-10-24", "America/Los_Angeles")).toBe(true);
    expect(isInToday(t, "2026-10-24", "Pacific/Auckland")).toBe(false);
  });

  it("itemsForView and viewCounts pass the zone through", () => {
    const items = [task("z", { dueAt: due })];
    expect(itemsForView(items, "today", null, "all", "2026-10-24", "America/Los_Angeles")).toHaveLength(1);
    expect(viewCounts(items, "all", "2026-10-24", "America/Los_Angeles").today).toBe(1);
  });
});

describe("tasksEntryView", () => {
  it("maps the My work only view to Next actions and leaves the rest", () => {
    expect(tasksEntryView("today")).toBe("next");
    expect(tasksEntryView("inbox")).toBe("inbox");
    expect(tasksEntryView("done")).toBe("done");
  });
});
