import { describe, expect, it } from "vitest";

import { dayKey } from "@/components/timeline/days";

import type { TaskRow } from "./api";
import { blockRowTask, interval, rowInterval, taskToBlockRow } from "./timelineAdapter";

const task = (over: Partial<TaskRow> = {}): TaskRow => ({
  id: "t1",
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s1",
  title: "Ship it",
  ...over,
});

describe("taskToBlockRow", () => {
  it("maps the title, the dates and the task itself", () => {
    const t = task({ start_date: "2026-08-03", due_at: "2026-08-07T12:00:00" });
    const row = taskToBlockRow(t);
    expect(row.id).toBe("t1");
    expect(row.label).toBe("Ship it");
    expect(dayKey(row.start as Date)).toBe("2026-08-03");
    expect(dayKey(row.end as Date)).toBe("2026-08-07");
    expect(row.derived).toBe(false);
    expect(row.tone).toBeNull();
    expect(blockRowTask(row)).toBe(t);
  });
  it("gives an undated task a row with no bar", () => {
    const row = taskToBlockRow(task());
    expect([row.start, row.end]).toEqual([null, null]);
  });
  it("folds the dates of the subtasks into a parent with none, and says so", () => {
    const row = taskToBlockRow(task(), [
      task({ id: "c1", start_date: "2026-08-05" }),
      task({ id: "c2", start_date: "2026-08-02" }),
    ]);
    expect(dayKey(row.start as Date)).toBe("2026-08-02");
    expect(dayKey(row.end as Date)).toBe("2026-08-05");
    expect(row.derived).toBe(true);
  });
  it("shows reversed dates as the span they imply", () => {
    expect(
      interval(task({ start_date: "2026-08-10", due_at: "2026-08-03T12:00:00" })),
    ).toEqual({ from: "2026-08-03", to: "2026-08-10" });
    expect(rowInterval(task({ start_date: "2026-08-10" }), [])).toEqual({
      from: "2026-08-10",
      to: "2026-08-10",
      derived: false,
    });
  });
});
