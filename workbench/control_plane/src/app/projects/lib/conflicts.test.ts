/**
 * WS-27bm S7c — the Conflicts panel's words, and the rule that it counts
 * nothing (`projects_ai_chat.md` §10.5 item 13).
 *
 * vitest here is node-env, so the panel itself cannot render. Its decisions
 * live in `conflicts.ts`, and this file holds them. The last block scans the
 * module and the panel for arithmetic, because "the browser computes
 * nothing" is a property of the source, not of any one render.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { ConflictRow, ConflictsReport } from "./api";
import {
  CONFLICT_KINDS,
  KIND_LABEL,
  SEVERITY_HUE,
  capNote,
  conflictRows,
  conflictsReportRows,
  countsLine,
  rowPeople,
  severityHue,
  windowLine,
} from "./conflicts";

const late: ConflictRow = {
  kind: "blocker_late",
  severity: "high",
  task_ids: ["t1", "k1"],
  people: [
    { email: "hal@example.test", name: "Hal" },
    { email: "gone@example.test", name: null },
  ],
  sentence: '"Order steel" was due on 2026-09-20 and is still open, and it blocks "Weld".',
  due_on: "2026-09-20",
};

const order: ConflictRow = {
  kind: "dependency_order",
  severity: "medium",
  task_ids: ["t2", "k2"],
  people: [],
  sentence: '"Paint" is planned from 2026-09-25, before "Weld", which blocks it, is due on 2026-09-27.',
  due_on: "2026-09-25",
};

function report(over: Partial<ConflictsReport> = {}): ConflictsReport {
  return {
    project_id: null,
    scope: "portfolio",
    include_subtree: true,
    horizon_days: 14,
    hr_visible: true,
    window: {
      starts_on: "2026-09-24",
      ends_on: "2026-10-08",
      days: 14,
      ignored_by: ["dependency_order", "blocker_late"],
    },
    partial: false,
    kinds: [...CONFLICT_KINDS],
    total: 2,
    by_kind: {
      dependency_order: 1,
      blocker_late: 1,
      parallel_person: 0,
      overcommitted: 0,
      absent_on_due: 0,
      over_concurrency: 0,
      leaving: 0,
    },
    truncated: false,
    rows: [order, late],
    ...over,
  };
}

describe("the rows", () => {
  it("keeps the server's rows in the server's order", () => {
    expect(conflictRows(report()).map((r) => r.kind)).toEqual([
      "dependency_order",
      "blocker_late",
    ]);
  });

  it("drops a malformed row and a kind it does not know", () => {
    const rows = [late, null, "x", { ...late, kind: "clash" }, { ...late, sentence: 3 }];
    expect(
      conflictRows(report({ rows: rows as unknown as ConflictRow[] })),
    ).toEqual([late]);
  });

  it("reads a missing list as no rows, never a throw", () => {
    expect(conflictRows(null)).toEqual([]);
    expect(conflictRows(report({ rows: undefined as unknown as ConflictRow[] }))).toEqual([]);
  });
});

describe("the words", () => {
  it("labels every one of the seven kinds", () => {
    expect(CONFLICT_KINDS).toHaveLength(7);
    for (const kind of CONFLICT_KINDS) expect(KIND_LABEL[kind]).toBeTruthy();
  });

  it("names people by name, and by address when there is no name", () => {
    expect(rowPeople(late)).toBe("Hal, gone@example.test");
    expect(rowPeople(order)).toBeNull();
  });

  it("prints the server's counts, and never a kind it did not send", () => {
    expect(countsLine(report())).toBe("1 out of order · 1 late blocker");
    const hidden = report({
      hr_visible: false,
      by_kind: { dependency_order: 2, blocker_late: 0, parallel_person: 1 },
    });
    const line = countsLine(hidden);
    expect(line).toBe("2 out of order · 1 parallel work");
    expect(line).not.toMatch(/overcommitted|ceiling|leaves|away/i);
  });

  it("says which rows the window binds", () => {
    expect(windowLine(report())).toBe(
      "Dated kinds: 2026-09-24 to 2026-10-08 (14 days). Dependencies are checked whenever they fall.",
    );
    expect(windowLine(report({ window: undefined as unknown as ConflictsReport["window"] }))).toBeNull();
  });

  it("says when the list was cut, by the server or by the panel", () => {
    expect(capNote(report(), 2)).toBeNull();
    expect(capNote(report({ total: 250, truncated: true }), 12)).toBe("Showing 12 of 250.");
    expect(capNote(report({ total: 30 }), 12)).toBe("Showing 12 of 30.");
  });
});

describe("the hues", () => {
  it("high is the destructive token and medium the warning token", () => {
    expect(SEVERITY_HUE).toEqual({ high: "red", medium: "amber" });
    expect(severityHue(late)).toBe("red");
    expect(severityHue(order)).toBe("amber");
  });

  it("reads an unknown severity as the milder one", () => {
    expect(severityHue({ ...late, severity: "urgent" as ConflictRow["severity"] })).toBe("amber");
  });
});

describe("the report section", () => {
  it("draws at most eight rows, each with its label and the server's sentence", () => {
    const many = Array.from({ length: 11 }, (_, i) => ({ ...late, task_ids: [`t${i}`] }));
    const rows = conflictsReportRows(many);
    expect(rows).toHaveLength(8);
    expect(rows[0]).toMatchObject({ label: "Late blocker", sentence: late.sentence, hue: "red" });
    expect(new Set(rows.map((r) => r.key)).size).toBe(8);
  });

  it("reads anything that is not a list as no rows", () => {
    expect(conflictsReportRows(undefined)).toEqual([]);
  });
});

describe("the browser counts nothing", () => {
  /**
   * The operators that would make a figure the browser's own. A `+` is left
   * out on purpose: every string here is built with it, and a numeric sum
   * needs one of the others or a `reduce` beside it.
   */
  const ARITHMETIC = /\.reduce\(|Math\.|\s[-*/]\s|\+=|-=|\+\+|--/;

  function code(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  }

  it("holds no arithmetic in the panel's module", () => {
    const source = readFileSync(join(__dirname, "conflicts.ts"), "utf-8");
    expect(code(source)).not.toMatch(ARITHMETIC);
  });

  it("holds no arithmetic in the panel itself", () => {
    // CRLF-normalised, for the reason `capacity.test.ts` gives.
    const source = readFileSync(
      join(__dirname, "..", "components", "AnalyticsPanels.tsx"),
      "utf-8",
    ).replace(/\r\n/g, "\n");
    const start = source.indexOf("export function ConflictsPanel");
    const end = source.indexOf("\n}\n", start);
    expect(start).toBeGreaterThan(-1);
    expect(code(source.slice(start, end))).not.toMatch(ARITHMETIC);
  });

  it("holds no copy of the dependency rule", () => {
    // The timeline keeps the browser's one copy, pinned to the server's by a
    // shared fixture. A third copy here would be the one that drifts.
    const source = code(readFileSync(join(__dirname, "conflicts.ts"), "utf-8"));
    expect(source).not.toMatch(/\binterval\(|from "\.\/timeline"/);
  });

  it("would catch a sum if one arrived", () => {
    expect("rows.reduce((n, r) => n + r.total, 0)").toMatch(ARITHMETIC);
    expect("const left = total - shown;").toMatch(ARITHMETIC);
    expect("n++").toMatch(ARITHMETIC);
  });
});
