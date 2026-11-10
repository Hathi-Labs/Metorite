/**
 * WS-27bn RA-6 — "Needs an owner", the words (`projects_reports.md` §6.8 B
 * and F). The server decides the count, the rows and `leads`
 * (`tests/unit/test_projects_report_forecast.py` and the R8 suite). These
 * tests pin how the client reads them, and that it orders nothing itself.
 */
import { describe, expect, it } from "vitest";

import type { LoadReport, NeedsOwner } from "./api";
import {
  NEEDS_OWNER_RULE,
  NEEDS_OWNER_TITLE,
  needsOwnerLine,
  needsOwnerOf,
  needsOwnerRows,
} from "./needsOwner";

function owner(over: Partial<NeedsOwner> = {}): NeedsOwner {
  return {
    total: 25,
    leads: true,
    max_rows: 20,
    tasks: Array.from({ length: 20 }, (_, i) => ({
      id: `t${i}`,
      task_number: i + 1,
      title: `Task ${i + 1}`,
      project_id: "p1",
      due_at: i === 0 ? "2026-10-20T00:00:00+00:00" : null,
    })),
    ...over,
  };
}

const load = (needs_owner?: NeedsOwner): LoadReport => ({
  project_id: "p1",
  scope: "node",
  total_tasks: 26,
  people: [],
  ...(needs_owner ? { needs_owner } : {}),
});

describe("needsOwnerOf", () => {
  it("reads the group the server sent", () => {
    expect(needsOwnerOf(load(owner()))?.total).toBe(25);
  });

  it("is nothing when none needs an owner, or an older server sent none", () => {
    expect(needsOwnerOf(load(owner({ total: 0, tasks: [] })))).toBeNull();
    expect(needsOwnerOf(load())).toBeNull();
    expect(needsOwnerOf(null)).toBeNull();
    expect(needsOwnerOf({ ...load(), needs_owner: { total: "x" } as never })).toBeNull();
  });
});

describe("needsOwnerRows", () => {
  it("shows five rows in the server's order, and counts the rest from the total", () => {
    const { shown, more } = needsOwnerRows(owner());
    expect(shown.map((r) => r.id)).toEqual(["t0", "t1", "t2", "t3", "t4"]);
    // 25 in total, 5 shown: the server named 20, and the total counts all.
    expect(more).toBe(20);
  });

  it("names each row by its number and title, and its due date in words", () => {
    const { shown } = needsOwnerRows(owner());
    expect(shown[0]).toEqual({
      key: "t0",
      id: "t0",
      label: "#1 Task 1",
      detail: "Due 20 Oct 2026",
    });
    expect(shown[1].detail).toBe("No due date");
  });

  it("a row with no number shows the title alone", () => {
    const { shown } = needsOwnerRows(
      owner({ total: 1, tasks: [{ id: "x", task_number: null, title: "Loose", project_id: "p", due_at: null }] })
    );
    expect(shown[0].label).toBe("Loose");
  });
});

describe("needsOwnerLine and the rule", () => {
  it("says the share of the open work, as in the owner's screenshot", () => {
    expect(needsOwnerLine(owner(), 26)).toBe("25 of 26 open tasks have no owner.");
    expect(needsOwnerLine(owner({ total: 1 }), 26)).toBe("1 of 26 open tasks has no owner.");
    expect(needsOwnerLine(owner({ total: 3 }), undefined)).toBe("3 open tasks have no owner.");
  });

  it("states the §6.8 F rule, and ranks no person", () => {
    expect(NEEDS_OWNER_TITLE).toBe("Needs an owner");
    expect(NEEDS_OWNER_RULE).toContain("no assignee and no open agent run");
    expect(NEEDS_OWNER_RULE.toLowerCase()).not.toMatch(/top|best|rank/);
  });
});
