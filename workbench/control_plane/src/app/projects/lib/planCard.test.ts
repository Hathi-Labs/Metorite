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
  afterCount,
  afterLabel,
  afterOptions,
  blankRow,
  isMarked,
  markLine,
  planIncomplete,
  planRowsFrom,
  planSubmit,
  waitsOnName,
  withAfter,
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

// ── WS-27bm S10: the card edits `after` (§16.2 rules 5 and 6) ───────────────

const CHAIN = planRowsFrom([
  { key: "t1", title: "Cut", owner: "a@x.io", effort_mins: 30, due: "2026-10-01" },
  { key: "t2", title: "Weld", owner: "a@x.io", effort_mins: 30, due: "2026-10-02", after: ["t1"] },
  { key: "t3", title: "Paint", owner: "a@x.io", effort_mins: 30, due: "2026-10-03", after: ["t2"] },
]);
const keys = (rows: { key: string }[]) => rows.map((r) => r.key);

describe("after is an input", () => {
  it("is not a read-only field", () => {
    expect(PLAN_READ_ONLY).not.toContain("after");
    expect([...PLAN_READ_ONLY].sort()).toEqual(["fit", "hours", "marks", "warnings"]);
  });
});

describe("afterOptions", () => {
  it("never offers the row itself or a row that waits on it", () => {
    // t2 waits on t1 directly and t3 through t2, so either tick is a cycle.
    expect(keys(afterOptions(CHAIN[0], CHAIN))).toEqual([]);
    expect(keys(afterOptions(CHAIN[1], CHAIN))).toEqual(["t1"]);
    expect(keys(afterOptions(CHAIN[2], CHAIN))).toEqual(["t1", "t2"]);
  });

  it("offers every other row when nothing waits", () => {
    const free = CHAIN.map((r) => ({ ...r, after: [] }));
    expect(keys(afterOptions(free[0], free))).toEqual(["t2", "t3"]);
  });

  it("ends on a cycle that is already in the data", () => {
    const loop = [{ ...CHAIN[0], after: ["t2"] }, CHAIN[1]];
    expect(keys(afterOptions(loop[0], loop))).toEqual([]);
  });
});

describe("withAfter", () => {
  it("ticks and clears a key, in row order", () => {
    expect(withAfter(CHAIN[2], CHAIN, "t1", true)).toEqual(["t1", "t2"]);
    expect(withAfter(CHAIN[2], CHAIN, "t2", false)).toEqual([]);
    expect(withAfter(CHAIN[2], CHAIN, "t2", true)).toEqual(["t2"]);
  });
});

describe("planSubmit with an edited after", () => {
  it("sends the edit, and strips the key of a dropped row", () => {
    const edited = CHAIN.map((r) => (r.key === "t3" ? { ...r, after: withAfter(r, CHAIN, "t1", true) } : r));
    const out = planSubmit({}, "Steps", edited);
    expect(out.tasks[2].after).toEqual(["t1", "t2"]);

    const dropped = edited.filter((r) => r.key !== "t1");
    const out2 = planSubmit({}, "Steps", dropped);
    expect(out2.tasks.find((t) => t.key === "t3")?.after).toEqual(["t2"]);
    expect(out2.tasks.find((t) => t.key === "t2")?.after).toEqual([]);
  });
});

describe("the Waits on count and name (fix round 1)", () => {
  it("counts only keys of rows still on the card", () => {
    const row = { ...CHAIN[2], after: ["t1", "t2", "gone"] };
    expect(afterCount(row, CHAIN)).toBe(2);
    expect(afterCount(row, CHAIN.filter((r) => r.key !== "t1"))).toBe(1);
  });

  it("names the control after its row, and starts with the visible label", () => {
    expect(waitsOnName(CHAIN[1])).toBe("Waits on (for Weld)");
    expect(waitsOnName({ ...CHAIN[1], title: "  " })).toBe("Waits on (for Untitled task)");
  });
});
