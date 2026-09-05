/**
 * WS-27t — the timeline's arithmetic and its two decided rules.
 *
 * The claims that matter here are not "does the bar render". They are the ones
 * where a plausible implementation is wrong in a way that looks fine:
 *
 * * **a bar covers its last day.** Stopping at the last day's left edge makes a
 *   one-day task a zero-width line and every span one day short — a chart that
 *   is subtly, consistently lying about durations.
 * * **equal dates are not a conflict** (D-PM-12). A blocker due the 10th and a
 *   task starting the 10th is the normal way people schedule a handover.
 *   Flagging it fires the warning on half a healthy plan, after which nobody
 *   reads it.
 * * **a subtask whose parent is off-window is promoted, not hidden.** Hiding it
 *   makes a filtered timeline silently drop work.
 * * **a parent with no dates borrows its children's span**, and says it did.
 *   Without that the depth-grouped default view looks empty for exactly the
 *   projects that use subtasks properly.
 * * **the cycle check is NOT reimplemented here.** `assert_no_block_cycle` owns
 *   it; a browser copy is the one that drifts.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { TaskRow } from "./api";
import { fromDayKey, shiftDay } from "./calendar";
import {
  LABEL_GAP_PX,
  labelSide,
  MIN_BAR_PX,
  PAD_DAYS,
  PX_PER_DAY,
  ROW_H,
  ZOOMS,
  ZOOM_ORDER,
  bandSpan,
  bar,
  barForSpan,
  canLink,
  timelineBands,
  conflictLabel,
  conflicts,
  dayAtPx,
  dayCells,
  dayPx,
  dayStep,
  dragRefusal,
  EDGE_HIT_PX,
  edgeMidpoint,
  edgePath,
  edgePoints,
  polylineLength,
  interval,
  type Point,
  roundedPath,
  isWeekend,
  monthCells,
  resizeEnd,
  resizeStart,
  rowInterval,
  spanFor,
  WINDOW_LIMIT_DAYS,
  type TimelineWindow,
  timelineRange,
  timelineRows,
  weekCells,
  windowCentre,
  windowFor,
  windowIncluding,
} from "./timeline";

/** A window's width in days, for the cap assertions. */
const spanDays = (w: TimelineWindow) =>
  Math.round(
    (fromDayKey(w.to).getTime() - fromDayKey(w.from).getTime()) / 86_400_000,
  );

/** The module's own source, with comments stripped.
 *
 *  Stripped because these assertions are about what the CODE does, and this
 *  module's prose is dense enough that "stays readable while it is wrong"
 *  matched a search for a `while` loop. A structural test that trips on its own
 *  documentation is a test people delete. */
const SOURCE = readFileSync(
  fileURLToPath(new URL("./timeline.ts", import.meta.url)),
  "utf8",
)
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");

const task = (over: Partial<TaskRow> = {}): TaskRow => ({
  id: "t1",
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s1",
  title: "Ship it",
  ...over,
});

/** A local-noon instant for a day key, so `due_at` fixtures are timezone-proof. */
const at = (key: string) => {
  const d = fromDayKey(key);
  d.setHours(12, 0, 0, 0);
  return d.toISOString();
};

const RANGE = timelineRange(
  [{ task: task({ start_date: "2026-08-01", due_at: at("2026-08-31") }), depth: 0, children: [] }],
  "2026-08-15",
);

// ── interval ────────────────────────────────────────────────────────────────

describe("interval", () => {
  it("spans start to due", () => {
    expect(interval(task({ start_date: "2026-08-03", due_at: at("2026-08-07") })))
      .toEqual({ from: "2026-08-03", to: "2026-08-07" });
  });

  it("is a point when only one date is known", () => {
    expect(interval(task({ start_date: "2026-08-03" })))
      .toEqual({ from: "2026-08-03", to: "2026-08-03" });
    expect(interval(task({ due_at: at("2026-08-07") })))
      .toEqual({ from: "2026-08-07", to: "2026-08-07" });
  });

  it("is null when the task has no dates", () => {
    expect(interval(task())).toBeNull();
  });

  it("normalises a backwards interval rather than dropping the task", () => {
    // The timeline is the one view that makes bad data obvious. Returning null
    // would hide exactly the task somebody needs to see.
    expect(interval(task({ start_date: "2026-08-20", due_at: at("2026-08-18") })))
      .toEqual({ from: "2026-08-18", to: "2026-08-20" });
  });

  it("never routes a start_date through the Date constructor", () => {
    // ⚠️ Structural, because the behavioural version only fails WEST of
    // Greenwich and CI runs one timezone — the lesson from WS-27q.
    expect(SOURCE).not.toMatch(/new Date\(\s*(task\.)?start_?[Dd]ate/);
  });
});

// ── rowInterval — D-PM-11's roll-up ─────────────────────────────────────────

describe("rowInterval", () => {
  it("prefers the task's own dates over its children's", () => {
    expect(
      rowInterval(task({ start_date: "2026-08-01", due_at: at("2026-08-02") }), [
        task({ id: "c", start_date: "2026-01-01", due_at: at("2026-12-31") }),
      ]),
    ).toEqual({ from: "2026-08-01", to: "2026-08-02", derived: false });
  });

  it("borrows the children's span when the parent has no dates", () => {
    // ⚠️ Without this a depth-grouped timeline looks EMPTY for exactly the
    // projects that use subtasks properly.
    expect(
      rowInterval(task(), [
        task({ id: "a", start_date: "2026-08-04", due_at: at("2026-08-06") }),
        task({ id: "b", start_date: "2026-08-02", due_at: at("2026-08-09") }),
      ]),
    ).toEqual({ from: "2026-08-02", to: "2026-08-09", derived: true });
  });

  it("marks a borrowed span as derived so the UI can say so", () => {
    const derived = rowInterval(task(), [task({ id: "a", start_date: "2026-08-04" })]);
    expect(derived?.derived).toBe(true);
  });

  it("ignores children that have no dates of their own", () => {
    expect(
      rowInterval(task(), [
        task({ id: "a" }),
        task({ id: "b", start_date: "2026-08-04" }),
      ]),
    ).toEqual({ from: "2026-08-04", to: "2026-08-04", derived: true });
  });

  it("is null when neither the parent nor any child has a date", () => {
    expect(rowInterval(task(), [task({ id: "a" })])).toBeNull();
  });
});

// ── timelineRows — D-PM-11's scoping ────────────────────────────────────────

describe("timelineRows", () => {
  it("gives every top-level task a row", () => {
    const rows = timelineRows([task({ id: "a" }), task({ id: "b" })]);
    expect(rows.map((r) => r.task.id)).toEqual(["a", "b"]);
  });

  it("folds a subtask under its parent instead of giving it a row", () => {
    const rows = timelineRows([
      task({ id: "parent" }),
      task({ id: "kid", parent_task_id: "parent" }),
    ]);
    expect(rows.map((r) => r.task.id)).toEqual(["parent"]);
    expect(rows[0].children.map((c) => c.id)).toEqual(["kid"]);
  });

  it("promotes a subtask whose parent is not in the window", () => {
    // ⚠️ Hidden, a filtered timeline silently drops work — the `undated`
    // failure one level down.
    const rows = timelineRows([task({ id: "orphan", parent_task_id: "elsewhere" })]);
    expect(rows.map((r) => r.task.id)).toEqual(["orphan"]);
    expect(rows[0].children).toEqual([]);
  });

  it("keeps the order it was given, which is the server's", () => {
    const rows = timelineRows([
      task({ id: "c" }), task({ id: "a" }), task({ id: "b" }),
    ]);
    expect(rows.map((r) => r.task.id)).toEqual(["c", "a", "b"]);
  });

  it("handles a task that claims itself as its parent", () => {
    // The gateway refuses this (assert_no_task_cycle), so it can only arrive
    // from corrupt data — and an infinite loop in the renderer is a worse
    // outcome than a row.
    const rows = timelineRows([task({ id: "a", parent_task_id: "a" })]);
    expect(rows.map((r) => r.task.id)).toEqual([]);
  });
});

// ── the range and the axis ──────────────────────────────────────────────────

describe("timelineRange", () => {
  it("pads the data's own span on both sides", () => {
    const range = timelineRange(
      [{ task: task({ start_date: "2026-08-10", due_at: at("2026-08-20") }), depth: 0, children: [] }],
      "2026-08-15",
    );
    expect(range.from).toBe("2026-08-03");
    expect(range.to).toBe("2026-08-27");
    expect(range.days).toBe(10 + 1 + PAD_DAYS * 2);
  });

  it("falls back to a fortnight around today when nothing is dated", () => {
    // An empty chart still needs an axis to read, and a zero-width one cannot
    // render at all.
    const range = timelineRange(
      [{ task: task(), depth: 0, children: [] }],
      "2026-08-15",
    );
    expect(range.from).toBe("2026-08-01");
    expect(range.to).toBe("2026-08-29");
    expect(range.widthPx).toBeGreaterThan(0);
  });

  it("covers a child's dates when only the child has them", () => {
    const range = timelineRange(
      [{ task: task(), depth: 0, children: [task({ id: "c", start_date: "2026-09-10" })] }],
      "2026-08-15",
    );
    expect(range.from <= "2026-09-10").toBe(true);
    expect(range.to >= "2026-09-10").toBe(true);
  });

  it("measures its width from its own day count", () => {
    expect(RANGE.widthPx).toBe(RANGE.days * PX_PER_DAY);
  });
});

describe("dayPx", () => {
  it("puts the first day at zero", () => {
    expect(dayPx(RANGE.from, RANGE)).toBe(0);
  });

  it("advances one day at a time", () => {
    expect(dayPx("2026-07-26", RANGE) - dayPx("2026-07-25", RANGE)).toBe(PX_PER_DAY);
  });

  it("survives a DST boundary without drifting a day", () => {
    // ⚠️ Millisecond arithmetic across a DST change is 23 or 25 hours, so an
    // unrounded division lands a fraction of a day off for every day after the
    // transition — and stays wrong for the rest of the chart.
    //
    // The range must STRADDLE a transition for this to bite: February to
    // August crosses the spring-forward in every northern DST zone, whereas
    // two days either side of midsummer are in the same regime and would pass
    // with the rounding removed. That was the first version of this test.
    const range = timelineRange(
      [{ task: task({ start_date: "2026-02-01", due_at: at("2026-08-31") }), depth: 0, children: [] }],
      "2026-05-01",
    );
    expect(dayPx("2026-08-02", range) - dayPx("2026-08-01", range)).toBe(PX_PER_DAY);
    expect(dayPx(range.to, range) + PX_PER_DAY).toBe(range.widthPx);
    expect(dayPx("2026-08-01", range) % PX_PER_DAY).toBe(0);
  });

  it("rounds the day count rather than trusting the millisecond division", () => {
    // ⚠️ Structural, and needed for the same reason as the `start_date` trap:
    // the behavioural test above can only fail in a timezone that HAS daylight
    // saving. In UTC — which is what CI runs — the drift is exactly zero and
    // the bug is invisible.
    const body = SOURCE.slice(SOURCE.indexOf("export function dayPx"));
    expect(body.slice(0, 300)).toContain("Math.round(");
  });
});

describe("monthCells", () => {
  it("labels each month once, in order", () => {
    const cells = monthCells(RANGE);
    expect(cells.map((c) => c.key)).toEqual(["2026-07", "2026-08", "2026-09"]);
    expect(cells[1].label).toBe("Aug 2026");
  });

  it("tiles the whole width with no gaps or overlaps", () => {
    // ⚠️ A clipped first cell is the easy bug: the range starts mid-July, so
    // that cell is short and every later cell shifts if it is not.
    const cells = monthCells(RANGE);
    expect(cells[0].px).toBe(0);
    for (let i = 1; i < cells.length; i += 1) {
      expect(cells[i].px).toBe(cells[i - 1].px + cells[i - 1].widthPx);
    }
    const last = cells[cells.length - 1];
    expect(last.px + last.widthPx).toBe(RANGE.widthPx);
  });

  it("handles a range inside a single month", () => {
    const range = timelineRange(
      [{ task: task({ start_date: "2026-08-10", due_at: at("2026-08-12") }), depth: 0, children: [] }],
      "2026-08-11",
    );
    const cells = monthCells(range);
    expect(cells.map((c) => c.key)).toEqual(["2026-08"]);
    expect(cells[0].widthPx).toBe(range.widthPx);
  });

  it("crosses a year boundary", () => {
    const range = timelineRange(
      [{ task: task({ start_date: "2026-12-20", due_at: at("2027-01-10") }), depth: 0, children: [] }],
      "2026-12-25",
    );
    expect(monthCells(range).map((c) => c.key)).toEqual(["2026-12", "2027-01"]);
  });
});

// ── bars ────────────────────────────────────────────────────────────────────

describe("bar", () => {
  it("covers the LAST day, not up to its left edge", () => {
    // ⚠️ The off-by-one that makes every span one day short and a one-day task
    // a zero-width line. Aug 10–12 is three days of chart.
    const drawn = bar(
      task({ start_date: "2026-08-10", due_at: at("2026-08-12") }), [], RANGE,
    );
    expect(drawn?.widthPx).toBe(3 * PX_PER_DAY);
  });

  it("draws a single-date task at least wide enough to click", () => {
    const drawn = bar(task({ due_at: at("2026-08-12") }), [], RANGE);
    expect(drawn?.singleDate).toBe(true);
    expect(drawn?.widthPx).toBeGreaterThanOrEqual(MIN_BAR_PX);
  });

  it("starts where the range says its first day starts", () => {
    const drawn = bar(task({ start_date: "2026-08-10" }), [], RANGE);
    expect(drawn?.leftPx).toBe(dayPx("2026-08-10", RANGE));
  });

  it("is null for a task with no dates and no dated children", () => {
    expect(bar(task(), [], RANGE)).toBeNull();
  });

  it("marks a bar borrowed from children as derived", () => {
    const drawn = bar(task(), [task({ id: "c", start_date: "2026-08-11" })], RANGE);
    expect(drawn?.derived).toBe(true);
  });
});

// ── D-PM-12 — the conflict rule ─────────────────────────────────────────────

describe("conflicts", () => {
  const blocker = (over: Partial<TaskRow> = {}) => task({ id: "blocker", ...over });
  const blocked = (over: Partial<TaskRow> = {}) => task({ id: "blocked", ...over });

  it("fires when the blocker finishes after the blocked task starts", () => {
    expect(
      conflicts(
        blocker({ start_date: "2026-08-01", due_at: at("2026-08-12") }),
        blocked({ start_date: "2026-08-10", due_at: at("2026-08-20") }),
      ),
    ).toBe(true);
  });

  it("does NOT fire when they merely touch", () => {
    // ⚠️ The decision that keeps the warning worth reading. A blocker due the
    // 10th and a task starting the 10th is a normal handover; flagging it
    // fires on half a healthy plan.
    expect(
      conflicts(
        blocker({ due_at: at("2026-08-10") }),
        blocked({ start_date: "2026-08-10" }),
      ),
    ).toBe(false);
  });

  it("does not fire on a well-ordered pair", () => {
    expect(
      conflicts(
        blocker({ due_at: at("2026-08-05") }),
        blocked({ start_date: "2026-08-10" }),
      ),
    ).toBe(false);
  });

  it("does not fire when either end has no dates", () => {
    // ⚠️ A warning that fires on absent data teaches people it means nothing.
    expect(conflicts(blocker(), blocked({ start_date: "2026-08-01" }))).toBe(false);
    expect(conflicts(blocker({ due_at: at("2026-08-20") }), blocked())).toBe(false);
  });

  it("never fires for a finished blocker", () => {
    // WS-27p: a resolved blocker blocks nothing. The same rule, applied to the
    // warning rather than to the badge.
    expect(
      conflicts(
        blocker({ due_at: at("2026-08-20"), completed_at: at("2026-08-01") }),
        blocked({ start_date: "2026-08-10" }),
      ),
    ).toBe(false);
  });

  it("uses the blocker's END, not its start", () => {
    // A blocker STARTING after the blocked task is fine as long as it finishes
    // first — unusual, but not a contradiction the chart should shout about.
    expect(
      conflicts(
        blocker({ start_date: "2026-08-12", due_at: at("2026-08-12") }),
        blocked({ start_date: "2026-08-14" }),
      ),
    ).toBe(false);
  });

  it("says what happened and that nothing was moved", () => {
    // ⚠️ D-PM-12 chose warn-over-push. The sentence has to say so, or users
    // assume the tool fixed it.
    const label = conflictLabel("Design sign-off");
    expect(label).toContain("Design sign-off");
    expect(label.toLowerCase()).toContain("nothing has been rescheduled");
  });

  it("writes nothing — the module holds no PATCH or reschedule", () => {
    // ⚠️ The structural half of D-PM-12. A later "helpful" auto-push would be
    // a decision reversal, not a refactor, and this is what makes it visible.
    expect(SOURCE).not.toMatch(/patchTask|projectsApi|fetch\(/);
  });
});

// ── arrows ──────────────────────────────────────────────────────────────────

describe("edgePoints — where an edge turns", () => {
  const barAt = (leftPx: number, widthPx: number) =>
    ({ leftPx, widthPx, singleDate: false, derived: false });

  it("routes forwards when there is room", () => {
    const pts = edgePoints(
      { bar: barAt(0, 50), row: 0 },
      { bar: barAt(200, 50), row: 2 },
    ) as Point[];
    // Out of the source's right edge, across, into the target's left edge.
    expect(pts[0]).toEqual({ x: 50, y: ROW_H / 2 });
    expect(pts.at(-1)).toEqual({ x: 200, y: 2 * ROW_H + ROW_H / 2 });
    expect(pts).toHaveLength(4);
    // The vertical leg is held clear of both bars, so a rounded corner never
    // curves into the thing it points at.
    expect(pts[1]?.x).toBeGreaterThan(50);
    expect(pts[1]?.x).toBeLessThan(200);
    expect(pts[1]?.x).toBe(pts[2]?.x);
  });

  it("draws a STRAIGHT line between two bars on one row", () => {
    // A dogleg between two bars on the same line is a corner drawn for nothing,
    // and it was drawn for nothing until now.
    const pts = edgePoints(
      { bar: barAt(0, 10), row: 3 },
      { bar: barAt(500, 10), row: 3 },
    ) as Point[];
    expect(pts).toHaveLength(2);
    expect(pts[0]?.y).toBe(pts[1]?.y);
  });

  it("routes around when the target starts before the source ends", () => {
    // The conflict geometry: a straight path would run backwards through both
    // bars. It stays followable while it is wrong, which is when it matters.
    const pts = edgePoints(
      { bar: barAt(100, 100), row: 0 },
      { bar: barAt(120, 60), row: 1 },
    ) as Point[];
    expect(pts).toHaveLength(6);
    expect(pts[0]).toEqual({ x: 200, y: ROW_H / 2 });
    expect(pts.at(-1)).toEqual({ x: 120, y: ROW_H + ROW_H / 2 });
    // It leaves rightwards and arrives leftwards — never backwards through a bar.
    expect(pts[1]!.x).toBeGreaterThan(pts[0]!.x);
    expect(pts.at(-2)!.x).toBeLessThan(pts.at(-1)!.x);
    // And it detours through the gap BETWEEN the two rows.
    const lane = pts[2]!.y;
    expect(lane).toBeGreaterThan(ROW_H / 2);
    expect(lane).toBeLessThan(ROW_H + ROW_H / 2);
  });

  it("is null when either end has no bar", () => {
    // ⚠️ An arrow to an undated task has nowhere to land, and drawing it to the
    // row's margin invents a date the task does not have.
    expect(edgePath({ bar: null, row: 0 }, { bar: barAt(0, 10), row: 1 })).toBeNull();
    expect(edgePath({ bar: barAt(0, 10), row: 0 }, { bar: null, row: 1 })).toBeNull();
  });

  it("centres on the row, so the arrow meets the middle of a bar", () => {
    const pts = edgePoints(
      { bar: barAt(0, 10), row: 3 },
      { bar: barAt(500, 10), row: 3 },
    ) as Point[];
    expect(pts[0]?.y).toBe(3 * ROW_H + ROW_H / 2);
  });
});

describe("roundedPath", () => {
  it("curves every interior corner and neither end", () => {
    // Right-angle joins are what make a dense dependency graph unreadable:
    // each corner is a hard visual stop, so six crossing arrows read as a
    // circuit diagram and no single one can be followed.
    const d = roundedPath([
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 100 },
    ]);
    expect(d.startsWith("M 0 0")).toBe(true);
    expect(d.endsWith("L 100 100")).toBe(true);
    expect(d.match(/Q/g)).toHaveLength(1);
  });

  it("CLAMPS the radius to half the shorter leg", () => {
    // Without the clamp a corner near the end of a short leg eats past the
    // next corner and the path folds back on itself — which would happen
    // constantly here, because the stubs either side of a bar are short.
    const d = roundedPath(
      [
        { x: 0, y: 0 },
        { x: 4, y: 0 },
        { x: 4, y: 100 },
      ],
      20,
    );
    // The fillet may not start before the path does.
    const firstL = /L (-?[\d.]+) /.exec(d);
    expect(Number(firstL?.[1])).toBeGreaterThanOrEqual(0);
    expect(d).not.toContain("-");
  });

  it("leaves a straight line straight", () => {
    const d = roundedPath([
      { x: 0, y: 0 },
      { x: 50, y: 0 },
    ]);
    expect(d).toBe("M 0 0 L 50 0");
    expect(d).not.toContain("Q");
  });

  it("does not curve a collinear vertex", () => {
    // A rounding artefact on a straight run reads as a kink in the line.
    const d = roundedPath([
      { x: 0, y: 0 },
      { x: 50, y: 0 },
      { x: 100, y: 0 },
    ]);
    expect(d).not.toContain("Q");
  });

  it("survives duplicate points instead of emitting NaN", () => {
    // A zero-length leg makes the direction vector undefined. `NaN` in a `d`
    // attribute drops the whole path silently — no error, no arrow.
    const d = roundedPath([
      { x: 10, y: 10 },
      { x: 10, y: 10 },
      { x: 60, y: 10 },
    ]);
    expect(d).not.toContain("NaN");
  });

  it("emits nothing for fewer than two points", () => {
    expect(roundedPath([])).toBe("");
    expect(roundedPath([{ x: 1, y: 1 }])).toBe("");
  });

  it("produces a path with no NaN for every edge shape", () => {
    // The integration guard: whatever `edgePoints` returns must be drawable.
    const barAt = (leftPx: number, widthPx: number) =>
      ({ leftPx, widthPx, singleDate: false, derived: false });
    const cases: Array<[number, number, number, number, number, number]> = [
      [0, 50, 0, 200, 50, 2], // forward, different rows
      [0, 10, 3, 500, 10, 3], // forward, same row
      [100, 100, 0, 120, 60, 1], // backward, adjacent rows
      [100, 100, 2, 0, 60, 0], // backward, upwards
      [100, 40, 1, 100, 40, 1], // exactly overlapping, one row
    ];
    for (const [l1, w1, r1, l2, w2, r2] of cases) {
      const d = edgePath(
        { bar: barAt(l1, w1), row: r1 },
        { bar: barAt(l2, w2), row: r2 },
      ) as string;
      expect(d).not.toContain("NaN");
      expect(d).not.toContain("undefined");
      expect(d.startsWith("M ")).toBe(true);
    }
  });
});

// ── canLink ─────────────────────────────────────────────────────────────────

describe("canLink", () => {
  it("allows a fresh dependency", () => {
    expect(canLink("a", "b", [])).toEqual({ ok: true });
  });

  it("refuses a task blocking itself", () => {
    expect(canLink("a", "a", [])).toEqual({
      ok: false,
      reason: "A task cannot block itself.",
    });
  });

  it("refuses a duplicate rather than creating a second identical edge", () => {
    expect(
      canLink("a", "b", [{ id: "l1", blocker_id: "a", blocked_id: "b" }]),
    ).toMatchObject({ ok: false });
  });

  it("allows the REVERSE of an existing edge through, for the gateway to refuse", () => {
    // ⚠️ a→b then b→a is a two-node cycle. It is refused, but by
    // `assert_no_block_cycle` — bounded, tested and shared with every other
    // caller. A second implementation here is the one that would drift.
    expect(
      canLink("b", "a", [{ id: "l1", blocker_id: "a", blocked_id: "b" }]),
    ).toEqual({ ok: true });
  });

  it("does not reimplement the cycle walk", () => {
    // The cycle walk's own vocabulary, not "does this file contain a loop" —
    // `monthCells` legitimately walks months with a `while`, and a structural
    // test that cannot tell the two apart is one that gets deleted the first
    // time it is wrong.
    expect(SOURCE).not.toMatch(/MAX_DEPTH|frontier|\bvisited\b/);
    const body = SOURCE.slice(SOURCE.indexOf("export function canLink"));
    expect(body).not.toMatch(/\bwhile\b|\bfor\s*\(/);
  });
});

// ── S5: bands ───────────────────────────────────────────────────────────────

describe("timelineBands", () => {
  const dated = (id: string, from: string, to: string) =>
    task({ id, start_date: from, due_at: at(to) });

  it("DROPS an empty band", () => {
    // The board keeps an empty status column, because a missing column there
    // reads as a missing state and the column is a drop target. A timeline
    // band is neither — it is a heading with nothing under it, on the canvas
    // whose vertical space is already the scarce axis.
    const bands = timelineBands([
      { key: "a", label: "Doing", tasks: [dated("t1", "2026-08-01", "2026-08-05")] },
      { key: "b", label: "Blocked", tasks: [] },
    ]);
    expect(bands.map((b) => b.key)).toEqual(["a"]);
  });

  it("counts the GROUP's tasks, not the drawn rows", () => {
    // A subtask folds into its parent and draws no row of its own, but it is
    // still a task in this band. A count that disagreed with the board's for
    // the same group would be the tell that there are two groupings.
    const parent = dated("p", "2026-08-01", "2026-08-10");
    const kid = task({
      id: "k", parent_task_id: "p", start_date: "2026-08-02", due_at: at("2026-08-04"),
    });
    const [band] = timelineBands([{ key: "a", label: "Doing", tasks: [parent, kid] }]);
    expect(band?.count).toBe(2);
    expect(band?.rows).toHaveLength(1);
  });

  it("folds each band's hierarchy independently", () => {
    const bands = timelineBands([
      { key: "a", label: "A", tasks: [dated("t1", "2026-08-01", "2026-08-05")] },
      { key: "b", label: "B", tasks: [dated("t2", "2026-08-06", "2026-08-09")] },
    ]);
    expect(bands.map((b) => b.rows.map((r) => r.task.id))).toEqual([["t1"], ["t2"]]);
  });
});

describe("bandSpan", () => {
  it("covers every task in the band", () => {
    const rows = timelineRows([
      task({ id: "a", start_date: "2026-08-04", due_at: at("2026-08-06") }),
      task({ id: "b", start_date: "2026-08-01", due_at: at("2026-08-03") }),
      task({ id: "c", start_date: "2026-08-09", due_at: at("2026-08-11") }),
    ]);
    expect(bandSpan(rows)).toEqual({ from: "2026-08-01", to: "2026-08-11" });
  });

  it("reaches into a parent's CHILDREN for their dates", () => {
    // A parent with no dates of its own still contributes the span its
    // subtasks carry, exactly as `rowInterval` gives it a derived bar. Without
    // this the roll-up would be shorter than the rows it summarises.
    const rows = timelineRows([
      task({ id: "p" }),
      task({
        id: "k", parent_task_id: "p", start_date: "2026-08-02", due_at: at("2026-08-08"),
      }),
    ]);
    expect(bandSpan(rows)).toEqual({ from: "2026-08-02", to: "2026-08-08" });
  });

  it("is null when nothing in the band has a date", () => {
    // The honest answer. Inventing a span from the chart's own edges would be
    // a bar that nobody's dates produced.
    expect(bandSpan(timelineRows([task({ id: "a" }), task({ id: "b" })]))).toBeNull();
  });

  it("ignores undated tasks rather than stretching to them", () => {
    const rows = timelineRows([
      task({ id: "a", start_date: "2026-08-04", due_at: at("2026-08-06") }),
      task({ id: "b" }),
    ]);
    expect(bandSpan(rows)).toEqual({ from: "2026-08-04", to: "2026-08-06" });
  });
});

// ── S3: the window ──────────────────────────────────────────────────────────

describe("windowFor", () => {
  it("mirrors the gateway's cap by READING it, not by remembering it", () => {
    // `WINDOW_LIMIT_DAYS` is a copy of a number that lives in Python, and a
    // copy goes stale and then lies — the failure `AGENTS.md` names by hand.
    // Read from the source rather than restated, the same way the gateway's
    // own colour test reads CATEGORY_HUES out of the TypeScript.
    const py = readFileSync(
      fileURLToPath(
        new URL(
          "../../../../../../apps/services/gateway/gateway/routes/projects/calendar.py",
          import.meta.url,
        ),
      ),
      "utf8",
    );
    const declared = /^MAX_WINDOW_DAYS\s*=\s*(\d+)/m.exec(py);
    expect(declared).not.toBeNull();
    expect(WINDOW_LIMIT_DAYS).toBe(Number(declared?.[1]));
  });

  it("stays inside the server's own cap at every zoom", () => {
    // `calendar.py` REFUSES a window over MAX_WINDOW_DAYS with a 422 rather
    // than clamping it, so a zoom that asks for too much draws an empty chart
    // rather than a narrow one. This is the mirror of a number in the gateway,
    // and it is the whole reason the constant is named here.
    for (const key of ZOOM_ORDER) {
      const w = windowFor(key, "2026-08-15");
      expect(spanDays(w)).toBeLessThanOrEqual(WINDOW_LIMIT_DAYS);
    }
  });

  it("widens with the zoom, and centres on the anchor", () => {
    const week = windowFor("week", "2026-08-15");
    const quarter = windowFor("quarter", "2026-08-15");
    expect(spanDays(quarter)).toBeGreaterThan(spanDays(week));
    expect(windowCentre(week)).toBe("2026-08-15");
    expect(windowCentre(quarter)).toBe("2026-08-15");
  });

  it("covers far more than the one month the timeline used to borrow", () => {
    // The bug: the timeline read the CALENDAR's window, so a task dragged past
    // the end of the month was not returned by the reload and the row silently
    // disappeared. Any zoom must hold a month with room either side.
    for (const key of ZOOM_ORDER) {
      expect(spanDays(windowFor(key, "2026-08-15"))).toBeGreaterThan(31 * 2);
    }
  });
});

describe("windowIncluding", () => {
  const base = windowFor("month", "2026-08-15");

  it("leaves a window that already covers the day alone", () => {
    // Identity, not a copy — the page compares by reference to decide whether
    // a refetch is needed at all.
    expect(windowIncluding(base, "2026-08-20")).toBe(base);
    expect(windowIncluding(base, base.from)).toBe(base);
    expect(windowIncluding(base, base.to)).toBe(base);
  });

  it("STRETCHES rather than slides, so nothing on screen leaves it", () => {
    const past = shiftDay(base.from, -5);
    const grown = windowIncluding(base, past);
    expect(grown.from).toBe(past);
    // The far end is untouched: a task dragged into the past must not push
    // everything after next month out of the chart.
    expect(grown.to).toBe(base.to);
  });

  it("slides instead when stretching would break the server's cap", () => {
    // A 422 draws nothing. A slid window draws the right thing minus the far
    // end, and the thing just moved is certainly in it.
    const wide = windowFor("quarter", "2026-08-15");
    const far = shiftDay(wide.to, 200);
    const slid = windowIncluding(wide, far);
    expect(spanDays(slid)).toBeLessThanOrEqual(WINDOW_LIMIT_DAYS);
    expect(far >= slid.from && far <= slid.to).toBe(true);
  });

  it("slides the other way for a day far in the past", () => {
    const wide = windowFor("quarter", "2026-08-15");
    const far = shiftDay(wide.from, -200);
    const slid = windowIncluding(wide, far);
    expect(spanDays(slid)).toBeLessThanOrEqual(WINDOW_LIMIT_DAYS);
    expect(far >= slid.from && far <= slid.to).toBe(true);
  });
});

describe("timelineRange over a window", () => {
  const window = windowFor("month", "2026-08-15");
  const rows = [
    { task: task({ start_date: "2026-08-10", due_at: at("2026-08-12") }), depth: 0, children: [] },
  ];

  it("covers the whole window, not just the data", () => {
    // Two bugs fall out of fitting to the data alone, and both read as a broken
    // chart rather than a wrong range: there is nowhere to drag TO, and moving
    // the earliest task slides every other bar sideways mid-gesture.
    const range = timelineRange(rows, "2026-08-15", "month", window);
    expect(range.from).toBe(window.from);
    expect(range.to).toBe(window.to);
  });

  it("does not move when the data inside it moves", () => {
    const before = timelineRange(rows, "2026-08-15", "month", window);
    const after = timelineRange(
      [{ task: task({ start_date: "2026-08-28", due_at: at("2026-08-30") }), depth: 0, children: [] }],
      "2026-08-15",
      "month",
      window,
    );
    expect(after.from).toBe(before.from);
    expect(after.widthPx).toBe(before.widthPx);
  });

  it("UNIONS with data that fell outside, so a loaded task is never off-chart", () => {
    // The window can move out from under a row that is already on screen.
    // Clipping it would hide a task the user can see in the left-hand column.
    const outside = [
      { task: task({ start_date: "2020-01-01", due_at: at("2020-01-05") }), depth: 0, children: [] },
    ];
    const range = timelineRange(outside, "2026-08-15", "month", window);
    expect(range.from < "2020-01-01").toBe(true);
    expect(range.to).toBe(window.to);
  });

  it("still fits the data when no window is given", () => {
    // The old behaviour is the fallback, and `timeline.test.ts`'s original
    // range assertions all run through it.
    const range = timelineRange(rows, "2026-08-15", "month");
    expect(range.from).toBe(shiftDay("2026-08-10", -PAD_DAYS));
  });
});

// ── S2: zoom ────────────────────────────────────────────────────────────────

describe("zoom", () => {
  it("keeps the default at the width the chart has always drawn", () => {
    // Every geometry fence above measures against PX_PER_DAY. If the default
    // zoom's width moves, all of them are silently re-baselined and the chart
    // re-lays-out inside whatever change did it.
    expect(ZOOMS.month.dayWidth).toBe(PX_PER_DAY);
    expect(timelineRange([], "2026-08-15").pxPerDay).toBe(PX_PER_DAY);
  });

  it("lays the same range out wider or narrower per zoom", () => {
    const rows = [
      { task: task({ start_date: "2026-08-01", due_at: at("2026-08-10") }), depth: 0, children: [] },
    ];
    const week = timelineRange(rows, "2026-08-05", "week");
    const quarter = timelineRange(rows, "2026-08-05", "quarter");

    // Same days, three widths — the range is the data's, the pixels are the zoom's.
    expect(week.days).toBe(quarter.days);
    expect(week.widthPx).toBe(week.days * ZOOMS.week.dayWidth);
    expect(quarter.widthPx).toBe(quarter.days * ZOOMS.quarter.dayWidth);
    expect(week.widthPx).toBeGreaterThan(quarter.widthPx);
  });

  it("draws a day tier only where a day column can hold a label", () => {
    // A day tier at 7px per day is seven pixels of unreadable text.
    for (const key of ZOOM_ORDER) {
      const spec = ZOOMS[key];
      if (spec.tier === "day") expect(spec.dayWidth).toBeGreaterThanOrEqual(28);
    }
  });

  it("measures every x from the range's own pxPerDay, not the constant", () => {
    const subject = task({ start_date: "2026-08-01", due_at: at("2026-08-03") });
    const range = timelineRange(
      [{ task: subject, depth: 0, children: [] }],
      "2026-08-02",
      "week",
    );
    // The bug this catches: a helper reaching for PX_PER_DAY while the chart
    // was laid out at another zoom. Every bar is then wrong by the ratio
    // between the two, which reads as a rendering bug rather than a unit one.
    expect(dayPx("2026-08-02", range) - dayPx("2026-08-01", range)).toBe(
      ZOOMS.week.dayWidth,
    );
    expect(bar(subject, [], range)?.widthPx).toBe(3 * ZOOMS.week.dayWidth);
  });
});

// ── S2: the drag → day arithmetic ───────────────────────────────────────────

describe("dayStep", () => {
  it("snaps to the NEAREST day, so a half-day drag resolves where it leans", () => {
    expect(dayStep(0, RANGE)).toBe(0);
    expect(dayStep(PX_PER_DAY, RANGE)).toBe(1);
    expect(dayStep(PX_PER_DAY * 0.6, RANGE)).toBe(1);
    expect(dayStep(PX_PER_DAY * 0.4, RANGE)).toBe(0);
  });

  it("goes backwards symmetrically", () => {
    // `Math.floor` would make a leftward drag lag a rightward one by a day —
    // the asymmetry nobody notices until they drag a bar back to where it was
    // and it lands a day early.
    expect(dayStep(-PX_PER_DAY * 2, RANGE)).toBe(-2);
    expect(dayStep(-PX_PER_DAY * 0.6, RANGE)).toBe(-1);
  });

  it("is measured in the range's own pixels", () => {
    const week = timelineRange([], "2026-08-15", "week");
    expect(dayStep(ZOOMS.week.dayWidth * 3, week)).toBe(3);
    // The same travel is more days when a day is narrower.
    expect(dayStep(ZOOMS.week.dayWidth * 3, RANGE)).toBeGreaterThan(3);
  });
});

describe("dayAtPx", () => {
  it("returns the day a pixel column belongs to", () => {
    expect(dayAtPx(0, RANGE)).toBe(RANGE.from);
    expect(dayAtPx(PX_PER_DAY, RANGE)).toBe(shiftDay(RANGE.from, 1));
    // Anywhere INSIDE a day's column is that day — floor, not round, or the
    // second half of every day reads as the next one.
    expect(dayAtPx(PX_PER_DAY * 1.9, RANGE)).toBe(shiftDay(RANGE.from, 1));
  });

  it("clamps rather than inventing days off either end", () => {
    // A cursor dragged past the chart's edge must land on a real day: the
    // create gesture writes whatever this returns straight into `start_date`.
    expect(dayAtPx(-500, RANGE)).toBe(RANGE.from);
    expect(dayAtPx(RANGE.widthPx + 500, RANGE)).toBe(RANGE.to);
  });
});

// ── S2: what a drag writes ──────────────────────────────────────────────────

describe("resizeStart", () => {
  const subject = task({ start_date: "2026-08-10", due_at: at("2026-08-20") });

  it("moves the start", () => {
    expect(resizeStart(subject, "2026-08-12")).toEqual({ start_date: "2026-08-12" });
  });

  it("CLAMPS at the due date instead of inverting the bar", () => {
    // Swapping the ends would write an inverted span as real dates, and the bar
    // turns inside out under the cursor on the way there. Stopping is the only
    // outcome the user could have meant.
    expect(resizeStart(subject, "2026-08-25")).toEqual({ start_date: "2026-08-20" });
  });

  it("writes nothing when the start did not move", () => {
    // A no-op PATCH is an activity row saying a task moved from the 10th to the
    // 10th, in the one place people look to find out what changed.
    expect(resizeStart(subject, "2026-08-10")).toBeNull();
  });

  it("gives a due-only task a start", () => {
    // The one case where a resize legitimately fills a null field: it is how an
    // open-ended task gains a span without opening the panel.
    expect(resizeStart(task({ due_at: at("2026-08-20") }), "2026-08-14")).toEqual({
      start_date: "2026-08-14",
    });
  });

  it("refuses a task with no dates at all", () => {
    expect(resizeStart(task(), "2026-08-14")).toBeNull();
  });
});

describe("resizeEnd", () => {
  const subject = task({ start_date: "2026-08-10", due_at: at("2026-08-20") });

  it("moves the due date and KEEPS its time of day", () => {
    // "Due Friday at 5" extended to Monday is due Monday at 5, not Monday at
    // midnight — the rule `rescheduleTo` already holds for a whole-bar move.
    const patch = resizeEnd(subject, "2026-08-25");
    expect(patch).not.toBeNull();
    const moved = new Date((patch as { due_at: string }).due_at);
    expect(moved.getDate()).toBe(25);
    expect(moved.getHours()).toBe(12);
  });

  it("clamps at the start date", () => {
    const patch = resizeEnd(subject, "2026-08-01");
    expect(new Date((patch as { due_at: string }).due_at).getDate()).toBe(10);
  });

  it("writes nothing when the day did not change", () => {
    expect(resizeEnd(subject, "2026-08-20")).toBeNull();
  });

  it("gives a start-only task a due date", () => {
    const patch = resizeEnd(task({ start_date: "2026-08-10" }), "2026-08-14");
    expect(new Date((patch as { due_at: string }).due_at).getDate()).toBe(14);
  });

  it("refuses a task with no dates at all", () => {
    expect(resizeEnd(task(), "2026-08-14")).toBeNull();
  });
});

describe("a resize moves one edge and PINS the other", () => {
  /**
   * The owner's bug, 2026-08-31, and the shape of my own test gap.
   *
   * `resizeStart` was tested on a due-only task and `resizeEnd` on a start-only
   * task — the two combinations that already worked. The other two were never
   * asked, and both were broken the same way: the moving edge was written, the
   * far edge was left implied, and a one-day bar RELOCATED instead of growing.
   *
   * So the assertion is the invariant rather than the field: apply the patch,
   * re-read the interval, and check the edge you did not touch has not moved.
   */
  const apply = (subject: TaskRow, patch: Record<string, string> | null) =>
    interval({ ...subject, ...patch } as TaskRow);

  it("extends a DUE-ONLY task rightwards instead of moving it", () => {
    const dueOnly = task({ due_at: at("2026-08-20") });
    const before = interval(dueOnly);
    const after = apply(dueOnly, resizeEnd(dueOnly, "2026-08-25"));

    expect(after?.from).toBe(before?.from); // the left edge did not move
    expect(after?.to).toBe("2026-08-25");
    // And it is a SPAN now, not a point that travelled.
    expect(after?.from).not.toBe(after?.to);
  });

  it("extends a START-ONLY task leftwards instead of moving it", () => {
    const startOnly = task({ start_date: "2026-08-20" });
    const before = interval(startOnly);
    const after = apply(startOnly, resizeStart(startOnly, "2026-08-15"));

    expect(after?.to).toBe(before?.to); // the right edge did not move
    expect(after?.from).toBe("2026-08-15");
    expect(after?.from).not.toBe(after?.to);
  });

  it("pins the far edge for every task shape and every edge", () => {
    // All four combinations, stated once. A task with both dates was never
    // broken; it is here so the rule reads as one rule.
    const shapes: Array<[string, TaskRow]> = [
      ["both", task({ start_date: "2026-08-10", due_at: at("2026-08-20") })],
      ["due only", task({ due_at: at("2026-08-20") })],
      ["start only", task({ start_date: "2026-08-10" })],
    ];
    for (const [name, subject] of shapes) {
      const span = interval(subject);
      if (!span) continue;

      const pulledRight = apply(subject, resizeEnd(subject, shiftDay(span.to, 4)));
      expect(pulledRight?.from, `${name}: right edge pull moved the start`).toBe(
        span.from,
      );

      const pulledLeft = apply(subject, resizeStart(subject, shiftDay(span.from, -4)));
      expect(pulledLeft?.to, `${name}: left edge pull moved the due date`).toBe(
        span.to,
      );
    }
  });

  it("agrees with the bar the drag PREVIEWED", () => {
    // The tell that this was wrong: the preview drew the correct span the whole
    // time and the bar jumped on release. Two geometries, one of them written.
    const shapes = [
      task({ start_date: "2026-08-10", due_at: at("2026-08-20") }),
      task({ due_at: at("2026-08-20") }),
      task({ start_date: "2026-08-10" }),
    ];
    for (const subject of shapes) {
      const span = interval(subject) as { from: string; to: string };

      const endDay = shiftDay(span.to, 3);
      const previewed = barForSpan(span.from, endDay, RANGE);
      const committed = apply(subject, resizeEnd(subject, endDay));
      expect(barForSpan(committed!.from, committed!.to, RANGE)).toEqual(previewed);

      const startDay = shiftDay(span.from, -3);
      const previewedLeft = barForSpan(startDay, span.to, RANGE);
      const committedLeft = apply(subject, resizeStart(subject, startDay));
      expect(barForSpan(committedLeft!.from, committedLeft!.to, RANGE)).toEqual(
        previewedLeft,
      );
    }
  });
});

describe("spanFor", () => {
  it("writes BOTH ends, so a task never passes through half-scheduled", () => {
    const patch = spanFor("2026-08-10", "2026-08-14");
    expect(patch.start_date).toBe("2026-08-10");
    expect(new Date(patch.due_at).getDate()).toBe(14);
  });

  it("orders the ends however the drag was swept", () => {
    // Right-to-left is the same gesture. Writing it unordered stores a span
    // that `interval` then silently un-inverts, so the chart looks right while
    // the stored dates are backwards.
    expect(spanFor("2026-08-14", "2026-08-10").start_date).toBe("2026-08-10");
  });
});

describe("dragRefusal", () => {
  it("refuses a DERIVED bar, which has no dates of its own to write", () => {
    expect(dragRefusal(barForSpan("2026-08-01", "2026-08-05", RANGE, true))).toMatch(
      /subtask/i,
    );
  });

  it("refuses a row with no bar", () => {
    expect(dragRefusal(null)).not.toBeNull();
  });

  it("allows a bar the task owns", () => {
    expect(dragRefusal(barForSpan("2026-08-01", "2026-08-05", RANGE))).toBeNull();
  });
});

describe("barForSpan", () => {
  it("agrees with `bar` for the same interval", () => {
    // The drag preview goes through `barForSpan` and the settled bar through
    // `bar`. A pixel of disagreement is a bar that jumps on release.
    const subject = task({ start_date: "2026-08-04", due_at: at("2026-08-09") });
    expect(barForSpan("2026-08-04", "2026-08-09", RANGE)).toEqual(
      bar(subject, [], RANGE),
    );
  });
});

// ── S2: the header's lower tier ─────────────────────────────────────────────

describe("dayCells", () => {
  const range = timelineRange(
    [{ task: task({ start_date: "2026-08-03", due_at: at("2026-08-16") }), depth: 0, children: [] }],
    "2026-08-10",
  );
  const cells = dayCells(range, "2026-08-10");

  it("draws one cell per day of the range, in order", () => {
    expect(cells).toHaveLength(range.days);
    expect(cells[0]?.day).toBe(range.from);
    expect(cells.at(-1)?.day).toBe(range.to);
  });

  it("puts every cell where dayPx says it goes", () => {
    // Two ways to compute an x is one way to have the shading a day out of step
    // with the bars it is shading.
    for (const cell of cells) expect(cell.px).toBe(dayPx(cell.day, range));
  });

  it("marks Saturday and Sunday, on a MONDAY week", () => {
    // 2026-08-08 is a Saturday, 08-09 a Sunday, 08-10 a Monday.
    expect(cells.find((c) => c.day === "2026-08-08")?.weekend).toBe(true);
    expect(cells.find((c) => c.day === "2026-08-09")?.weekend).toBe(true);
    expect(cells.find((c) => c.day === "2026-08-10")?.weekend).toBe(false);
    expect(cells.find((c) => c.day === "2026-08-10")?.weekStart).toBe(true);
    // `isWeekend` and the cell flag are one rule, read twice.
    expect(isWeekend("2026-08-08")).toBe(true);
    expect(isWeekend("2026-08-10")).toBe(false);
  });

  it("marks exactly one day today", () => {
    expect(cells.filter((c) => c.today)).toHaveLength(1);
    expect(cells.find((c) => c.today)?.day).toBe("2026-08-10");
  });
});

describe("weekCells", () => {
  const range = timelineRange(
    [{ task: task({ start_date: "2026-08-03", due_at: at("2026-08-25") }), depth: 0, children: [] }],
    "2026-08-10",
  );
  const cells = weekCells(range, "2026-08-10");

  it("tiles the range exactly, with no gap and no overhang", () => {
    // A week cell that overhangs the chart is how a Gantt header ends up one
    // day out of step with the bars underneath it.
    expect(cells[0]?.from).toBe(range.from);
    expect(cells.at(-1)?.to).toBe(range.to);
    expect(cells.reduce((sum, c) => sum + c.widthPx, 0)).toBe(range.widthPx);
    for (let i = 1; i < cells.length; i += 1) {
      expect(cells[i]?.px).toBe((cells[i - 1]?.px ?? 0) + (cells[i - 1]?.widthPx ?? 0));
    }
  });

  it("starts its full weeks on a Monday", () => {
    // The first cell is clipped by the range, so the rule is checked on the
    // ones after it. `getDay()` is 1 on Monday.
    for (const cell of cells.slice(1)) {
      expect(fromDayKey(cell.from).getDay()).toBe(1);
    }
  });

  it("marks the week containing today, and only that one", () => {
    const marked = cells.filter((c) => c.today);
    expect(marked).toHaveLength(1);
    expect("2026-08-10" >= (marked[0]?.from ?? "")).toBe(true);
    expect("2026-08-10" <= (marked[0]?.to ?? "")).toBe(true);
  });
});

describe("edgeMidpoint — where the remove control sits", () => {
  it("is half way ALONG a dogleg, not half way between its ends", () => {
    // The whole reason this function exists. On an L the midpoint of the
    // endpoints floats in empty space beside the line, and a control drawn
    // there sits on top of something the edge is not about.
    const points: Point[] = [
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 100 },
    ];
    const mid = edgeMidpoint(points);
    // Total length 200, so half is 100 — exactly the corner.
    expect(mid).toEqual({ x: 100, y: 0 });
    // The naive answer would have been the average of the two ends.
    const naive = { x: 50, y: 50 };
    expect(mid).not.toEqual(naive);
  });

  it("lands at the centre of a straight edge", () => {
    expect(edgeMidpoint([{ x: 10, y: 5 }, { x: 30, y: 5 }])).toEqual({ x: 20, y: 5 });
  });

  it("interpolates INSIDE the segment that contains the half-way mark", () => {
    // Lengths 10 then 30: half of 40 is 20, which is 10 into the second leg.
    const mid = edgeMidpoint([
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 40, y: 0 },
    ]);
    expect(mid).toEqual({ x: 20, y: 0 });
  });

  it("walks the six-point conflict route rather than averaging it", () => {
    // The shape `edgePoints` returns when the blocked bar starts before the
    // blocker ends — out, down into the lane, back, and in. The midpoint must
    // land on the lane, which is the only part of that route with room for a
    // control.
    const stub = { singleDate: false, derived: false };
    const from = { bar: { leftPx: 100, widthPx: 60, ...stub }, row: 0 };
    const to = { bar: { leftPx: 20, widthPx: 60, ...stub }, row: 1 };
    const points = edgePoints(from, to);
    expect(points).not.toBeNull();
    expect(points!.length).toBe(6);
    const mid = edgeMidpoint(points!);
    const lane = (ROW_H / 2 + (ROW_H + ROW_H / 2)) / 2;
    expect(mid!.y).toBeCloseTo(lane, 5);
  });

  it("refuses a degenerate input rather than inventing a point", () => {
    expect(edgeMidpoint([])).toBeNull();
    expect(edgeMidpoint([{ x: 1, y: 1 }])).toBeNull();
  });

  it("survives a zero-length edge", () => {
    // Two identical points: the length is 0 and the division would be NaN.
    expect(edgeMidpoint([{ x: 7, y: 7 }, { x: 7, y: 7 }])).toEqual({ x: 7, y: 7 });
  });

  it("ignores a repeated vertex instead of dividing by zero", () => {
    const mid = edgeMidpoint([
      { x: 0, y: 0 },
      { x: 0, y: 0 },
      { x: 20, y: 0 },
    ]);
    expect(mid).toEqual({ x: 10, y: 0 });
  });
});

describe("polylineLength", () => {
  it("sums the segments, and a 3-4-5 stays honest", () => {
    expect(polylineLength([{ x: 0, y: 0 }, { x: 3, y: 4 }])).toBe(5);
    expect(
      polylineLength([{ x: 0, y: 0 }, { x: 3, y: 4 }, { x: 3, y: 14 }]),
    ).toBe(15);
  });

  it("is zero for fewer than two points", () => {
    expect(polylineLength([])).toBe(0);
    expect(polylineLength([{ x: 5, y: 5 }])).toBe(0);
  });
});

describe("EDGE_HIT_PX — the reason the arrow is hittable", () => {
  it("is wide enough to hit and no wider than the stub", () => {
    // A drawn edge is 1.5px. Below about 8 the hover is pixel-hunting. Above
    // the 12px stub the hit area reaches back over the bar it leaves, where
    // the bar's own drag zones have to win.
    expect(EDGE_HIT_PX).toBeGreaterThanOrEqual(8);
    expect(EDGE_HIT_PX).toBeLessThanOrEqual(12);
  });
});

describe("labelSide — a narrow bar's label stays on the canvas", () => {
  // The measured regression. `TimelineBar` positioned the label at
  // `leftPx + widthPx + 8` with a `max-w` and no bound, and its own header
  // claimed the right is "where there is always room". At 1440 on 2026-09-05 a
  // bar near the right edge put its label 57px past the scroller and the name
  // was clipped — on exactly the bar too narrow to carry its title inside.
  const CANVAS = 1000;

  it("keeps the label on the right when there is room", () => {
    const place = labelSide({ leftPx: 100, widthPx: 24 }, CANVAS);
    expect(place.side).toBe("right");
    expect(place.offsetPx).toBe(132);
  });

  it("caps the width at the room actually there, never past the edge", () => {
    // 40px of canvas left: the label may take 40, not its 240 maximum.
    const place = labelSide({ leftPx: 940, widthPx: 12 }, CANVAS);
    expect(place.offsetPx + place.maxWidthPx).toBeLessThanOrEqual(CANVAS);
  });

  it("flips to the left when the right cannot hold a readable label", () => {
    const place = labelSide({ leftPx: 950, widthPx: 20 }, CANVAS);
    expect(place.side).toBe("left");
    // A CSS `right`, so the label's right edge sits one gap left of the bar.
    expect(place.offsetPx).toBe(CANVAS - 950 + LABEL_GAP_PX);
  });

  it("never lets a flipped label cross the left edge either", () => {
    const place = labelSide({ leftPx: 30, widthPx: 4 }, 60);
    const rightEdge = 60 - place.offsetPx;
    if (place.side === "left") expect(rightEdge - place.maxWidthPx).toBeGreaterThanOrEqual(0);
    else expect(place.offsetPx + place.maxWidthPx).toBeLessThanOrEqual(60);
  });

  it("prefers the right on a tie, so an ordinary chart never moves a label", () => {
    // Equal room both sides. Keeping the right means the common reading order
    // stays bar-then-name, and only a genuinely cramped bar reads name-then-bar.
    const place = labelSide({ leftPx: 100, widthPx: 800 }, 1000 + 8 + 92 - 8);
    expect(place.side).toBe("right");
  });

  it("holds the invariant across the whole canvas", () => {
    // The property, rather than four examples: wherever the bar sits, the label
    // stays inside the chart on both sides.
    for (let left = 0; left <= CANVAS - 10; left += 7) {
      const place = labelSide({ leftPx: left, widthPx: 10 }, CANVAS);
      if (place.side === "right") {
        expect(place.offsetPx + place.maxWidthPx).toBeLessThanOrEqual(CANVAS);
      } else {
        expect(CANVAS - place.offsetPx - place.maxWidthPx).toBeGreaterThanOrEqual(0);
      }
      expect(place.maxWidthPx).toBeGreaterThanOrEqual(0);
    }
  });
});
