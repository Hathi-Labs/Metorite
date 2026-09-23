/**
 * WS-27bm S7a — the Capacity panel's words, and the rule that it counts
 * nothing (`projects_ai_chat.md` §10.3 item 8).
 *
 * vitest here is node-env, so the panel itself cannot render. Its decisions
 * live in `capacity.ts`, and this file holds them. The last block scans the
 * module and the panel for arithmetic, because "the browser computes nothing"
 * is a property of the source, not of any one render.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { CapacityReport, CapacityRow } from "./api";
import {
  capacityReportRows,
  capacityRows,
  hasHrHalf,
  hoursLine,
  rowLabel,
  rowWarnings,
  skillLine,
  windowsLine,
} from "./capacity";

const task: CapacityRow = {
  assignee: "ana@example.test",
  name: "Ana",
  kind: "person",
  in_directory: true,
  open_tasks: 3,
  overdue: 1,
  due_next_7d: 1,
  later: 1,
  estimated_hours_left: 2,
  estimated: 1,
};

const withHours: CapacityRow = {
  ...task,
  all_work: { open_tasks: 5, overdue: 1, unestimated: 2, in_progress: 2 },
  contracted_hours_per_week: 40,
  working_hours_horizon: 80,
  committed_hours_horizon: 22.5,
  spare_hours_horizon: 57.5,
  hours_basis: true,
  hours_note: null,
  absences: [{ kind: "leave", starts_on: "2026-09-28", ends_on: "2026-09-29" }],
  end_date: "2026-10-01",
  leaving_in_window: true,
  max_concurrent_tasks: 1,
  over_concurrency: true,
  skills: [
    { skill: "CAD", level: "expert" },
    { skill: "Python", level: null },
  ],
};

const noEstimates: CapacityRow = {
  ...task,
  all_work: { open_tasks: 3, overdue: 0, unestimated: 3, in_progress: 0 },
  contracted_hours_per_week: 40,
  working_hours_horizon: 80,
  hours_basis: false,
  hours_note: "3 open tasks with no estimate — hours-based signals are off.",
};

const unassigned: CapacityRow = {
  assignee: null,
  name: null,
  kind: "unassigned",
  in_directory: false,
  open_tasks: 4,
  overdue: 0,
  due_next_7d: 0,
  later: 4,
  estimated_hours_left: 0,
  estimated: 0,
};

describe("capacity rows", () => {
  it("reads a malformed response as no rows, not as a crash", () => {
    expect(capacityRows(null)).toEqual([]);
    expect(capacityRows({ rows: null } as unknown as CapacityReport)).toEqual([]);
  });

  it("names the unassigned row, and a person by their directory name", () => {
    expect(rowLabel(unassigned)).toBe("Unassigned");
    expect(rowLabel(task)).toBe("Ana");
    expect(rowLabel({ ...task, name: null })).toBe("ana@example.test");
    expect(rowLabel({ ...task, kind: "agent", assignee: "agent:triage", name: "triage" })).toBe(
      "agent:triage"
    );
  });
});

describe("the HR half", () => {
  it("draws no hours at all when the server sent none", () => {
    expect(hasHrHalf(task)).toBe(false);
    expect(hoursLine(task, 14)).toBeNull();
    expect(rowWarnings(task)).toEqual([]);
    expect(skillLine(task)).toBeNull();
  });

  it("prints the server's spare figure verbatim", () => {
    const line = hoursLine(withHours, 14);
    expect(line?.text).toBe("57.5h spare");
    expect(line?.title).toContain("80h of working time");
    expect(line?.title).toContain("22.5h committed");
  });

  it("says why there are no hours instead of printing zero", () => {
    const line = hoursLine(noEstimates, 14);
    expect(line?.text).toBe("No hours");
    expect(line?.title).toBe(noEstimates.hours_note);
    expect(line?.text).not.toMatch(/0h/);
  });

  it("names absences, the end date and a broken ceiling", () => {
    expect(rowWarnings(withHours)).toEqual([
      "Away 2026-09-28 to 2026-09-29",
      "Leaves 2026-10-01",
      "Over a ceiling of 1 in progress",
    ]);
    expect(skillLine(withHours)).toBe("CAD (expert), Python");
  });

  it("says Left, not Leaves, for an end date already past", () => {
    // Review round 1: an engagement that ended last week has not "left"
    // in the future. The horizon's first day is today, from the server.
    expect(rowWarnings(withHours, "2026-10-05")).toContain("Left 2026-10-01");
    expect(rowWarnings(withHours, "2026-09-23")).toContain("Leaves 2026-10-01");
    // No date to compare with keeps the future tense.
    expect(rowWarnings(withHours)).toContain("Leaves 2026-10-01");
  });
});

describe("the windows", () => {
  it("prints both, each with its own purpose", () => {
    const line = windowsLine({
      windows: {
        week: { starts_on: "2026-09-21", ends_on: "2026-09-27", used_for: "pill" },
        horizon: {
          starts_on: "2026-09-23",
          ends_on: "2026-10-07",
          days: 14,
          used_for: "spare_hours_and_at_risk",
        },
      },
    } as CapacityReport);
    expect(line).toContain("Spare hours: 2026-09-23 to 2026-10-07 (14 days)");
    expect(line).toContain("Pill: week of 2026-09-21 to 2026-09-27");
  });
});

describe("the report section", () => {
  it("keeps the unassigned row past the cap", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({
      ...task,
      assignee: `p${i}@example.test`,
      name: `P${i}`,
    }));
    const rows = capacityReportRows([...many, unassigned]);
    expect(rows).toHaveLength(9);
    expect(rows[rows.length - 1].name).toBe("Unassigned");
  });

  it("marks hours only where the server sent them", () => {
    const [a, b, c] = capacityReportRows([withHours, noEstimates, task]);
    expect(a.aside).toBe("57.5h spare");
    expect(b.aside).toBe("no hours");
    expect(c.aside).toBeUndefined();
  });
});

describe("the browser counts nothing", () => {
  /**
   * The operators that would make a figure the browser's own. A `+` is left
   * out on purpose: every string here is built with it, and a numeric sum
   * needs one of the others or a `reduce` beside it.
   */
  const ARITHMETIC = /\.reduce\(|Math\.|\s[-*/]\s|\+=|-=/;

  function code(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  }

  it("holds no arithmetic in the panel's module", () => {
    const source = readFileSync(join(__dirname, "capacity.ts"), "utf-8");
    expect(code(source)).not.toMatch(ARITHMETIC);
  });

  it("holds no arithmetic in the panel itself", () => {
    // CRLF-normalised: on a Windows checkout the `"\n}\n"` end marker below
    // never matched, the slice ran to the end of the file, and the scan
    // failed on chart code that is not the panel's.
    const source = readFileSync(
      join(__dirname, "..", "components", "AnalyticsPanels.tsx"),
      "utf-8"
    ).replace(/\r\n/g, "\n");
    const start = source.indexOf("export function CapacityPanel");
    const end = source.indexOf("\n}\n", start);
    expect(start).toBeGreaterThan(-1);
    expect(code(source.slice(start, end))).not.toMatch(ARITHMETIC);
  });

  it("would catch a sum if one arrived", () => {
    expect("rows.reduce((n, r) => n + r.open_tasks, 0)").toMatch(ARITHMETIC);
    expect("const spare = working - committed;").toMatch(ARITHMETIC);
  });
});
