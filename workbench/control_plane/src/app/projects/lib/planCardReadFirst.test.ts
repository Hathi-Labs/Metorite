/**
 * The plan card's read-first view (owner, 2026-10-18).
 *
 * The owner approved nine changes to the plan card. This file holds the pure
 * half of each one. The card's clicks run in `e2e/plan-card.spec.ts`, and its
 * default markup in `components/planCardBody.test.ts`.
 *
 * ⚠️ The golden test at the end is the fence on "do not change what gets
 * submitted". It replays one set of edits through the OLD card's own
 * operations (copied here from `genUITemplates.tsx` before the change) and
 * through the new card's, and compares the two answers byte for byte.
 */
import { describe, expect, it } from "vitest";

import {
  type PlanRow,
  CYCLE_BADGE,
  NO_PHASE,
  NO_PHASE_KEY,
  afterChoices,
  afterOptions,
  answeredPlan,
  keptRows as keep,
  planBlocked,
  planCycle,
  priorityLine,
  wholeDays,
  withAfter,
  PLAN_CANCEL,
  WORKDAY_MINS,
  blankRow,
  createLabel,
  effortIn,
  effortLabel,
  effortMins,
  effortUnit,
  footerNote,
  keptRows,
  ownerLabel,
  planDay,
  planGroups,
  planIncomplete,
  planOwners,
  planRowsFrom,
  planSubmit,
  planSubmitTicked,
  planSummary,
  planTimeline,
  planOutcome,
  workingStart,
  TIMELINE_MAX_PX,
  TIMELINE_MIN_ROWS,
  rowBadges,
  rowProblem,
  rowSpan,
  shiftDay,
  summaryParts,
  toggled,
  withOwner,
  withShift,
} from "./planCard";

const TASKS = [
  { key: "t1", title: "Build the memory store", owner: "ayush@x.io", effort_mins: 240,
    start: "2026-10-22", due: "2026-10-24", phase: "Foundation", impact: 4, urgency: 3, effort: 2 },
  { key: "t2", title: "Design the recall API", owner: "", effort_mins: 120,
    start: "2026-10-23", due: "2026-10-23", phase: "Foundation" },
  { key: "t3", title: "Define the schema", owner: "nithin@x.io", effort_mins: 360,
    start: "2026-10-22", due: "2026-10-23", phase: "Foundation", marks: ["short of hours"],
    hours: "short 4 h", fit: "Postgres" },
  { key: "t4", title: "Load test the retrieval path", owner: "nithin@x.io", effort_mins: 480,
    start: "2026-10-27", due: "2026-10-28", after: ["t1"], phase: "Rollout",
    marks: ["no skill match", "short of hours"], warnings: ["Not on the skills."] },
  { key: "t5", title: "Ship behind a flag", owner: "Priya", effort_mins: 720,
    due: "2026-11-15", after: ["t2", "t4"], phase: "Rollout", important: true },
];
const rows = () => planRowsFrom(TASKS);

describe("effort shows in hours or days, never minutes like 360", () => {
  it("formats minutes as h and d", () => {
    expect(effortLabel(360)).toBe("6h");
    expect(effortLabel(480)).toBe("1d");
    expect(effortLabel(720)).toBe("1.5d");
    expect(effortLabel(960)).toBe("2d");
    expect(effortLabel(240)).toBe("4h");
    expect(effortLabel(90)).toBe("1h 30m");
    expect(effortLabel(45)).toBe("45m");
    expect(effortLabel(2760)).toBe("46h");
    expect(effortLabel(0)).toBe("0m");
  });

  it("uses the app's working day, 480 minutes", () => {
    expect(WORKDAY_MINS).toBe(480);
  });

  it("edits in the unit it shows, and comes back to the same minutes", () => {
    for (const mins of [15, 45, 60, 90, 240, 360, 480, 720, 960, 2760]) {
      const unit = effortUnit(mins);
      expect(effortMins(effortIn(mins, unit), unit)).toBe(mins);
    }
    expect(effortUnit(360)).toBe("h");
    expect(effortUnit(480)).toBe("d");
    expect(effortUnit(45)).toBe("m");
    expect(effortMins(1.5, "d")).toBe(720);
    expect(effortMins(2, "h")).toBe(120);
    expect(effortMins(-3, "h")).toBe(0);
  });
});

describe("dates are short day chips", () => {
  it("writes the day first, and a year only outside this one", () => {
    expect(planDay("2026-10-22", 2026)).toBe("22 Oct");
    expect(planDay("2027-01-03", 2026)).toBe("3 Jan 2027");
    expect(planDay("nonsense", 2026)).toBe("nonsense");
  });

  it("draws a span, or one day when the row starts and ends on it", () => {
    const [t1, t2, , , t5] = rows();
    expect(rowSpan(t1, 2026)).toBe("22 Oct → 24 Oct");
    expect(rowSpan(t2, 2026)).toBe("23 Oct");
    // No start: the due date alone.
    expect(rowSpan(t5, 2026)).toBe("15 Nov");
  });

  it("shortens an address to a name for the owner chip", () => {
    expect(ownerLabel("priya.sharma@x.io")).toBe("Priya Sharma");
    expect(ownerLabel("Priya")).toBe("Priya");
  });
});

describe("the summary line", () => {
  it("counts the ticked tasks, the owners, the span and the hours", () => {
    const s = planSummary(rows());
    expect(s).toEqual({ tasks: 5, owners: 3, from: "2026-10-22", to: "2026-11-15", mins: 1920 });
    expect(summaryParts(s, 2026)).toEqual(["5 tasks", "3 owners", "22 Oct → 15 Nov", "4d"]);
  });

  it("follows the ticks", () => {
    const kept = keptRows(rows(), new Set(["t5", "t4"]));
    expect(summaryParts(planSummary(kept), 2026)).toEqual(["3 tasks", "2 owners", "22 Oct → 24 Oct", "1.5d"]);
    expect(summaryParts(planSummary([]), 2026)).toEqual(["0 tasks", "no owners"]);
  });
});

describe("the timeline strip (owner report, 2026-10-19)", () => {
  it("spans each bar from its start to its due date, on the plan's own span", () => {
    const t = planTimeline(rows(), 2026)!;
    expect(t.from).toBe("2026-10-22");
    expect(t.to).toBe("2026-11-15");
    const days = 25;
    expect(t.bars[0]).toMatchObject({ key: "t1", left: 0, width: 3 / days, derived: false, point: false });
    expect(t.bars[0].dates).toBe("22 Oct → 24 Oct");
    expect(t.bars[1]).toMatchObject({ key: "t2", left: 1 / days, width: 1 / days });
  });

  it("works a row with no start back from its due date by its effort, in working days, and draws it faint", () => {
    const t = planTimeline(rows(), 2026)!;
    // t5: 720 minutes is 1.5 working days, so two. It is due on Sunday 15 Nov,
    // and the count skips Saturday 14 Nov, so the bar starts on Friday 13 Nov.
    expect(t.bars[4]).toMatchObject({ key: "t5", left: 22 / 25, width: 3 / 25, derived: true, point: false });
    expect(t.bars[4].dates).toBe("13 Nov → 15 Nov (start from effort)");
  });

  it("counts working days back across a weekend", () => {
    const day = (iso: string) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 86_400_000;
    // Due Tuesday 27 Oct 2026, three working days: Fri 23, Mon 26, Tue 27.
    expect(workingStart(day("2026-10-27"), 3)).toBe(day("2026-10-23"));
    expect(workingStart(day("2026-10-27"), 1)).toBe(day("2026-10-27"));
    // Due Wednesday 28 Oct, two days: Tue 27 and Wed 28, no weekend between.
    expect(workingStart(day("2026-10-28"), 2)).toBe(day("2026-10-27"));
  });

  it("draws a row with no start and no effort as a dot, never a bar of a made-up length", () => {
    const t = planTimeline([...rows().slice(0, 4), { ...rows()[4], effort_mins: 0 }], 2026)!;
    expect(t.bars[4]).toMatchObject({ point: true, width: 0, left: 24 / 25 });
    expect(t.bars[4].dates).toBe("due 15 Nov");
  });

  it("follows the list's order, under its phases", () => {
    const mixed = planRowsFrom([
      { ...TASKS[3], phase: "Rollout" },
      { ...TASKS[0], phase: "Foundation" },
      { ...TASKS[4], phase: "Rollout" },
      { ...TASKS[1], phase: "" },
    ]);
    // planGroups: Rollout first (it shows first), then Foundation, then no phase.
    expect(planTimeline(mixed, 2026)!.bars.map((b) => b.key)).toEqual(["t4", "t5", "t1", "t2"]);
  });

  it("puts a small marker on a marked row only, and never colours the bar", () => {
    const t = planTimeline(rows(), 2026)!;
    expect(t.bars.map((b) => b.marked)).toEqual([false, false, true, true, false]);
  });

  it("drops every marker when every row is marked: a plan of marks is not all yellow", () => {
    const all = rows().map((r) => ({ ...r, owner: r.owner || "x@y.io", marks: ["no skill match"] }));
    const t = planTimeline(all, 2026)!;
    expect(t.bars.every((b) => !b.marked)).toBe(true);
  });

  it("grows with the rows, and stops at the cap", () => {
    expect(planTimeline(rows(), 2026)!.rowPx).toBe(8);
    const many = planRowsFrom(Array.from({ length: 19 }, (_, i) => ({
      key: `k${i}`, title: `Task ${i}`, owner: "a@b.io", effort_mins: 600,
      due: `2026-11-${String(i + 1).padStart(2, "0")}` })));
    const t = planTimeline(many, 2026)!;
    expect(t.rowPx).toBe(5);
    expect(t.bars.length * t.rowPx).toBeLessThanOrEqual(TIMELINE_MAX_PX);
    // Past 32 rows the rows get thinner still, and the strip never passes the cap.
    for (const n of [33, 48, 60, 120]) {
      const long = planRowsFrom(Array.from({ length: n }, (_, i) => ({
        key: `k${i}`, title: `Task ${i}`, owner: "a@b.io", effort_mins: 60,
        due: `2026-12-${String((i % 28) + 1).padStart(2, "0")}` })));
      const lt = planTimeline(long, 2026)!;
      expect(lt.bars.length * lt.rowPx, String(n)).toBeLessThanOrEqual(TIMELINE_MAX_PX);
    }
  });

  it("draws faint week lines on a span of two weeks or more", () => {
    const t = planTimeline(rows(), 2026)!;
    // 22 Oct 2026 is a Thursday: Mondays 26 Oct, 2 Nov, 9 Nov.
    expect(t.weeks).toEqual([4 / 25, 11 / 25, 18 / 25]);
  });

  it("hides when it says nothing: under three dated rows, or every bar on the same days", () => {
    expect(planTimeline([], 2026)).toBeNull();
    expect(planTimeline(rows().slice(0, TIMELINE_MIN_ROWS - 1), 2026)).toBeNull();
    const same = rows().slice(0, 3).map((r) => ({ ...r, start: "2026-10-22", due: "2026-10-24" }));
    expect(planTimeline(same, 2026)).toBeNull();
  });
});

describe("the answered plan folds to what happened (owner report, 2026-10-19)", () => {
  it("names the tasks sent, or a cancel, and never an empty line", () => {
    const sent = `Review plan — ${JSON.stringify(planSubmit({ name: "Memory" }, "Memory", rows()))}`;
    expect(planOutcome(rows(), sent)).toBe("5 tasks sent for approval");
    const one = `Review plan — ${JSON.stringify(planSubmit({ name: "Memory" }, "Memory", rows().slice(0, 1)))}`;
    expect(planOutcome(rows(), one)).toBe("1 task sent for approval");
    expect(planOutcome(rows(), PLAN_CANCEL)).toBe("cancelled, nothing created");
    expect(planOutcome(rows(), "")).toBe("");
  });
});

describe("the ticks and the footer", () => {
  it("counts the ticked rows in the footer button", () => {
    let off = new Set<string>();
    expect(createLabel(keptRows(rows(), off).length)).toBe("Create 5 tasks");
    off = toggled(off, "t2", true);
    off = toggled(off, "t4", true);
    expect(createLabel(keptRows(rows(), off).length)).toBe("Create 3 tasks");
    off = toggled(off, "t2", false);
    expect(createLabel(keptRows(rows(), off).length)).toBe("Create 4 tasks");
    expect(createLabel(1)).toBe("Create 1 task");
  });

  it("says one approval follows, and nothing is made before it", () => {
    expect(footerNote(7)).toContain("one approval creates all 7 tasks");
    expect(footerNote(2)).toContain("one approval creates both tasks");
    expect(footerNote(1)).toContain("one approval creates the task");
    expect(footerNote(7)).toContain("Nothing is created before it");
    expect(footerNote(0)).toBe("Tick at least one task to create it.");
  });

  it("does not let an unticked row block the plan", () => {
    const rs = [...rows(), blankRow(rows())];
    expect(planIncomplete("P", rs)).not.toBe("");
    expect(planIncomplete("P", keptRows(rs, new Set(["new1"])))).toBe("");
    expect(rowProblem(rs[5])).toBe("Needs a title");
    expect(rowProblem(rs[0])).toBe("");
  });

  it("Cancel sends no submit: the answer has no ' — '", () => {
    expect(PLAN_CANCEL.includes(" — ")).toBe(false);
  });
});

describe("badges", () => {
  it("names each mark in a word or two, once", () => {
    const [t1, t2, t3, t4] = rows();
    expect(rowBadges(t1)).toEqual([]);
    expect(rowBadges(t3)).toEqual([{ label: "Overloaded", tone: "warning" }]);
    expect(rowBadges(t4).map((b) => b.label)).toEqual(["Skill gap", "Overloaded"]);
    // No owner: the owner chip says "Unassigned", and no warning shows.
    expect(rowBadges(t2)).toEqual([]);
    expect(rowBadges({ ...t3, owner: "" })).toEqual([]);
  });

  it("keeps a mark it does not know, in its own words", () => {
    const [t1] = rows();
    expect(rowBadges({ ...t1, marks: ["away on the due date"] })).toEqual([
      { label: "Away on the due date", tone: "warning" },
    ]);
  });
});

describe("phases fold", () => {
  it("groups the rows under each phase, in the order each first shows", () => {
    const g = planGroups(rows())!;
    expect(g.map((x) => [x.phase, x.rows.map((r) => r.key)])).toEqual([
      ["Foundation", ["t1", "t2", "t3"]],
      ["Rollout", ["t4", "t5"]],
    ]);
  });

  it("puts the rows with no phase last, under one label", () => {
    const rs = rows().map((r) => (r.key === "t2" ? { ...r, phase: "" } : r));
    expect(planGroups(rs)!.map((x) => x.phase)).toEqual(["Foundation", "Rollout", NO_PHASE]);
  });

  it("draws one flat list when no row carries a phase", () => {
    expect(planGroups(rows().map((r) => ({ ...r, phase: "" })))).toBeNull();
  });

  it("never sends the phase back", () => {
    const out = planSubmit({}, "P", rows());
    for (const t of out.tasks) expect("phase" in t).toBe(false);
  });
});

describe("bulk edits", () => {
  it("sets one owner on the picked rows, or clears it", () => {
    const picked = new Set(["t2", "t5"]);
    const next = withOwner(rows(), picked, "sam@x.io");
    expect(next.map((r) => r.owner)).toEqual(["ayush@x.io", "sam@x.io", "nithin@x.io", "nithin@x.io", "sam@x.io"]);
    expect(withOwner(next, picked, "").filter((r) => !r.owner).map((r) => r.key)).toEqual(["t2", "t5"]);
  });

  it("moves the start and the due date of the picked rows together", () => {
    const next = withShift(rows(), new Set(["t1", "t5"]), 3);
    expect([next[0].start, next[0].due]).toEqual(["2026-10-25", "2026-10-27"]);
    // A row with no start keeps none.
    expect([next[4].start, next[4].due]).toEqual(["", "2026-11-18"]);
    expect(next[1]).toEqual(rows()[1]);
    expect(withShift(rows(), new Set(["t1"]), -30)[0].due).toBe("2026-09-24");
    expect(withShift(rows(), new Set(["t1"]), 0)).toEqual(rows());
  });

  it("crosses a month and a year by calendar days", () => {
    expect(shiftDay("2026-12-30", 3)).toBe("2027-01-02");
    expect(shiftDay("2028-02-28", 1)).toBe("2028-02-29");
    expect(shiftDay("", 3)).toBe("");
  });

  it("suggests the owners already on the plan, once each", () => {
    expect(planOwners([...rows(), { ...rows()[0], owner: "AYUSH@x.io" }])).toEqual(["ayush@x.io", "nithin@x.io", "Priya"]);
  });
});

// ── The golden test: the same edits submit the same bytes ───────────────────

/** The old card's operations, as `genUITemplates.tsx` held them before
 *  2026-10-18: a cell edit by index, a dropped row, an added row. */
function oldCard(project: Record<string, unknown>, name: string, start: PlanRow[]) {
  let r = start;
  return {
    setCell: (i: number, key: keyof PlanRow, v: unknown) => {
      r = r.map((x, k) => (k === i ? { ...x, [key]: v } : x));
    },
    drop: (i: number) => {
      r = r.filter((_, k) => k !== i);
    },
    add: () => {
      r = [...r, blankRow(r)];
    },
    index: (key: string) => r.findIndex((x) => x.key === key),
    submit: (label: string) => `${label} — ${JSON.stringify(planSubmit(project, name, r))}`,
  };
}

/** The new card's state and operations, as `PlanCardBody` holds them. */
function newCard(project: Record<string, unknown>, name: string, start: PlanRow[]) {
  let r = start;
  let off = new Set<string>();
  return {
    setCell: (key: string, field: keyof PlanRow, v: unknown) => {
      r = r.map((x) => (x.key === key ? { ...x, [field]: v } : x));
    },
    untick: (key: string) => {
      off = toggled(off, key, true);
    },
    retick: (key: string) => {
      off = toggled(off, key, false);
    },
    add: () => {
      const row = blankRow(r);
      r = [...r, row];
      return row.key;
    },
    owner: (keys: string[], who: string) => {
      r = withOwner(r, new Set(keys), who);
    },
    shift: (keys: string[], days: number) => {
      r = withShift(r, new Set(keys), days);
    },
    submit: (label: string) => `${label} — ${JSON.stringify(planSubmitTicked(project, name, r, off))}`,
  };
}

describe("the submit payload is byte-identical to the old card's for the same edits", () => {
  const project = { name: "Memory", parent: "Metorite", description: "" };

  it("a title, an owner, an effort, dates, a dropped row and an added row", () => {
    const o = oldCard(project, "Memory v2", rows());
    const n = newCard(project, "Memory v2", rows());
    // Edit a title and an effort (1.5 days in the new card's unit).
    o.setCell(0, "title", "Build the memory store and write path");
    n.setCell("t1", "title", "Build the memory store and write path");
    o.setCell(2, "effort_mins", 720);
    n.setCell("t3", "effort_mins", effortMins(1.5, "d"));
    // Give the unassigned row an owner, then bulk-assign two rows.
    o.setCell(1, "owner", "sam@x.io");
    n.setCell("t2", "owner", "sam@x.io");
    o.setCell(0, "owner", "priya@x.io");
    o.setCell(2, "owner", "priya@x.io");
    n.owner(["t1", "t3"], "priya@x.io");
    // Move two rows by 2 days: the old card typed each date by hand.
    o.setCell(0, "start", "2026-10-24");
    o.setCell(0, "due", "2026-10-26");
    o.setCell(4, "due", "2026-11-17");
    n.shift(["t1", "t5"], 2);
    // Drop t4 (its link from t5 drops with it) and add a row.
    o.drop(o.index("t4"));
    n.untick("t4");
    o.add();
    const added = n.add();
    o.setCell(o.index("new1"), "title", "Write the FAQ");
    o.setCell(o.index("new1"), "due", "2026-11-20");
    n.setCell(added, "title", "Write the FAQ");
    n.setCell(added, "due", "2026-11-20");
    expect(n.submit("Review plan")).toBe(o.submit("Review plan"));
  });

  it("a row unticked and ticked again is in the payload, unchanged", () => {
    const o = oldCard(project, "Memory", rows());
    const n = newCard(project, "Memory", rows());
    n.untick("t2");
    n.retick("t2");
    expect(n.submit("Review plan")).toBe(o.submit("Review plan"));
  });

  it("pins the bytes of one plan, so planSubmit itself cannot drift", () => {
    const n = newCard({ name: "P" }, "P", planRowsFrom([TASKS[0], TASKS[4]]));
    n.untick("t5");
    expect(n.submit("Review plan")).toBe(
      'Review plan — {"project":{"name":"P"},"tasks":[{"key":"t1","title":"Build the memory store",' +
        '"owner":"ayush@x.io","effort_mins":240,"start":"2026-10-22","due":"2026-10-24","after":[],' +
        '"important":null,"leveraged":null,"impact":4,"urgency":3,"effort":2}]}',
    );
  });
});

// ── Review of #1136 ─────────────────────────────────────────────────────────

describe("Waits on cannot build a circle through an unticked row", () => {
  const base = (key: string, after: string[] = []) =>
    ({ key, title: key, owner: "a@x.io", effort_mins: 60, due: "2026-10-30", after });

  it("replays the review's probe: untick, edit, tick again", () => {
    let rs = planRowsFrom([base("t1"), base("t2", ["t1"]), base("t3")]);
    let off = new Set(["t1"]);
    const row = (k: string) => rs.find((r) => r.key === k)!;
    const set = (k: string, after: string[]) => { rs = rs.map((r) => (r.key === k ? { ...r, after } : r)); };
    // t1 is unticked. t2 still waits on it, so t2 is no choice for t1.
    expect(afterChoices(row("t1"), rs, keep(rs, off)).map((r) => r.key)).toEqual(["t3"]);
    set("t1", withAfter(row("t1"), keep(rs, off), "t3", true));
    // t3: t1 waits on it, and t2 waits on t1. Neither is a choice now.
    expect(afterChoices(row("t3"), rs, keep(rs, off)).map((r) => r.key)).toEqual([]);
    // The old rule read only the ticked rows. It lost the link through the
    // unticked t1, offered t2, and a tick made a circle: the defect.
    expect(afterOptions(row("t3"), keep(rs, off)).map((r) => r.key)).toEqual(["t2"]);
    off = new Set();
    expect(planCycle(keep(rs, off)).size).toBe(0);
    expect(planBlocked("P", keep(rs, off))).toBe("");
  });

  it("names a circle and holds the submit when one comes back", () => {
    const rs = planRowsFrom([base("t1", ["t3"]), base("t2", ["t1"]), base("t3", ["t2"]), base("t4")]);
    expect([...planCycle(rs)].sort()).toEqual(["t1", "t2", "t3"]);
    expect(planBlocked("P", rs)).toContain("in a circle");
    // An unticked row breaks the circle, so the rest can go.
    expect(planCycle(keep(rs, new Set(["t2"]))).size).toBe(0);
    expect(CYCLE_BADGE).toBe("Waits in a circle");
  });
});

describe("Move dates takes whole days only", () => {
  it("reads only a whole number", () => {
    expect(wholeDays("2")).toBe(2);
    expect(wholeDays(" -3 ")).toBe(-3);
    for (const bad of ["1.5", "-1.5", "", "2x", "e"]) expect(wholeDays(bad)).toBeNull();
  });

  it("moves nothing by a half day", () => {
    expect(withShift(rows(), new Set(["t1"]), 1.5)).toEqual(rows());
    expect(withShift(rows(), new Set(["t1"]), -1.5)).toEqual(rows());
  });
});

describe("a phase named like the no-phase group", () => {
  it("stays its own group", () => {
    const rs = rows().map((r, i) => ({ ...r, phase: i === 0 ? NO_PHASE : i === 1 ? "" : r.phase }));
    const g = planGroups(rs)!;
    const named = g.find((x) => x.key === `phase:${NO_PHASE}`)!;
    const none = g.find((x) => x.key === NO_PHASE_KEY)!;
    expect(named.rows.map((r) => r.key)).toEqual(["t1"]);
    expect(none.rows.map((r) => r.key)).toEqual(["t2"]);
    expect(g[g.length - 1].key).toBe(NO_PHASE_KEY);
  });
});

describe("the priority score shows in the open row", () => {
  it("says the score and that it is a sorting aid", () => {
    // The server sends the score (forms.py _plan_row).
    const [t1] = planRowsFrom([{ ...TASKS[0], priority: 24 }]);
    expect(priorityLine(t1)).toBe("Priority 24 · a sorting aid");
    expect(priorityLine({ ...t1, priority: null })).toBe("");
  });
});

describe("an answered card draws as the member left it", () => {
  it("reads a submit: the sent rows with their edits, the rest unticked", () => {
    const sent = planSubmit({ name: "P" }, "Memory v2",
      [{ ...rows()[0], owner: "sam@x.io" }, rows()[2], { ...blankRow(rows()), title: "Extra", due: "2026-11-01" }]);
    const past = answeredPlan("Memory", rows(), `Review plan — ${JSON.stringify(sent)}`)!;
    expect(past.done).toBe("sent");
    expect(past.name).toBe("Memory v2");
    expect(past.rows[0].owner).toBe("sam@x.io");
    // The read-only fields stay: the phase and the marks of a sent row.
    expect(past.rows[2].phase).toBe("Foundation");
    expect(past.rows[2].marks).toEqual(["short of hours"]);
    expect([...past.off].sort()).toEqual(["t2", "t4", "t5"]);
    expect(past.rows.map((r) => r.key)).toContain("new1");
  });

  it("reads Cancel, and draws nothing new with no answer", () => {
    expect(answeredPlan("M", rows(), PLAN_CANCEL)!.done).toBe("cancelled");
    expect(answeredPlan("M", rows(), "")).toBeNull();
  });

  it("reads an answer that is not a JSON object as not submitted, and never throws", () => {
    // Review of #1136: `null` threw inside the card's state initialiser.
    for (const body of ["null", "[1]", '"s"', "7"]) {
      expect(answeredPlan("M", rows(), `Review plan — ${body}`)!.done).toBe("cancelled");
    }
  });
});
