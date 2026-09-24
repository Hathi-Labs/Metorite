/**
 * The plan card's pure decisions (WS-27bm S7d, spec §10.6 item 8).
 *
 * The submit carries `start`, `after` and the three scores. The fit and the
 * hours are read-only: no input edits them and the submit never sends them.
 * A marked row shows its mark, and a mark never blocks the submit.
 */
import { describe, expect, it } from "vitest";

import {
  PLAN_EDIT_COLS,
  PLAN_READ_ONLY,
  afterLabel,
  blankRow,
  isMarked,
  markLine,
  planIncomplete,
  planRowsFrom,
  planSubmit,
} from "./planCard";

const TASKS = [
  {
    key: "t1", title: "Order the steel", owner: "priya@x.io", effort_mins: 120,
    due: "2026-10-10", impact: 5, urgency: 4, effort: 2, priority: 40,
    fit: "welding", hours: "fits, 12 h spare before the plan", marks: [],
  },
  {
    key: "t2", title: "Weld the frame", owner: "priya@x.io", effort_mins: 240,
    start: "2026-10-05", due: "2026-10-12", after: ["t1"], impact: 3, urgency: 3, effort: 3,
    priority: 27, fit: "no skill match", hours: "short 4 h by 2026-10-12",
    marks: ["no skill match", "short of hours"], warnings: ["Away (holiday) on the due date"],
  },
];

describe("planSubmit", () => {
  it("carries the key, start, after and the three scores", () => {
    const rows = planRowsFrom(TASKS);
    const out = planSubmit({ name: "Frame", parent: "Ops" }, " Frame v2 ", rows);
    expect(out.project).toEqual({ name: "Frame v2", parent: "Ops" });
    expect(out.tasks[1]).toMatchObject({
      key: "t2", start: "2026-10-05", due: "2026-10-12", after: ["t1"],
      impact: 3, urgency: 3, effort: 3,
    });
    expect(out.tasks[0]).toMatchObject({ key: "t1", start: null, impact: 5, urgency: 4, effort: 2 });
  });

  it("never sends the fit, the hours, the marks or the warnings", () => {
    const out = planSubmit({}, "Frame", planRowsFrom(TASKS));
    for (const task of out.tasks) {
      for (const key of ["fit", "hours", "marks", "warnings", "priority"]) {
        expect(key in task).toBe(false);
      }
    }
  });

  it("drops a link to a row the member removed", () => {
    const rows = planRowsFrom(TASKS).filter((r) => r.key !== "t1");
    const out = planSubmit({}, "Frame", rows);
    expect(out.tasks[0].after).toEqual([]);
  });
});

describe("the read-only fields", () => {
  it("are never an editable column", () => {
    const editable = new Set(PLAN_EDIT_COLS.map((c) => c.key));
    for (const key of PLAN_READ_ONLY) expect(editable.has(key)).toBe(false);
    expect([...editable]).toEqual(["title", "owner", "effort_mins", "start", "due"]);
  });

  it("stay absent when the server sent none (no HR grant)", () => {
    const [row] = planRowsFrom([{ key: "t1", title: "A", owner: "a@x.io", effort_mins: 30, due: "2026-10-01" }]);
    expect(row.fit).toBeUndefined();
    expect(row.hours).toBeUndefined();
    expect(isMarked(row)).toBe(false);
  });
});

describe("a marked row", () => {
  it("shows its marks and warnings on one line", () => {
    const rows = planRowsFrom(TASKS);
    expect(isMarked(rows[0])).toBe(false);
    expect(isMarked(rows[1])).toBe(true);
    expect(markLine(rows[1])).toBe("no skill match · short of hours · Away (holiday) on the due date");
  });

  it("never blocks the submit", () => {
    expect(planIncomplete("Frame", planRowsFrom(TASKS))).toBe("");
  });
});

describe("planIncomplete", () => {
  it("wants the four fields and a start on or before the due date", () => {
    const rows = planRowsFrom(TASKS);
    expect(planIncomplete(" ", rows)).not.toBe("");
    expect(planIncomplete("Frame", [])).not.toBe("");
    expect(planIncomplete("Frame", [{ ...rows[0], owner: "" }])).not.toBe("");
    expect(planIncomplete("Frame", [{ ...rows[0], effort_mins: 0 }])).not.toBe("");
    expect(planIncomplete("Frame", [{ ...rows[1], start: "2026-10-13" }])).toBe(
      "A task starts after its due date.",
    );
  });
});

describe("rows the member adds", () => {
  it("get a key no row carries", () => {
    const rows = planRowsFrom(TASKS);
    const one = blankRow(rows);
    const two = blankRow([...rows, one]);
    expect(one.key).toBe("new1");
    expect(two.key).toBe("new2");
    expect(one.fit).toBeUndefined();
  });

  it("name their blockers by title", () => {
    const rows = planRowsFrom(TASKS);
    expect(afterLabel(rows[1], rows)).toBe("Order the steel");
    expect(afterLabel(rows[1], [rows[1]])).toBe("");
  });
});
