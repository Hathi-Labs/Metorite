/**
 * WS-27bn R3b — the Rebalance panel's words, and the rule that they count
 * nothing. Spec: `project-docs/specs/projects_reports.md` §8 R3b.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { RebalanceReport, RebalanceTask } from "./api";
import {
  REBALANCE_HR_HINT,
  helpersLine,
  holderLine,
  pickupLine,
  rebalanceCapNote,
  rebalancePickups,
  rebalanceTasks,
} from "./rebalance";

const task = (over: Partial<RebalanceTask> = {}): RebalanceTask => ({
  task_id: "t1",
  title: "Weld the gantry",
  project_name: "Rig",
  due_on: "2026-09-27",
  shortfall_hours: 12,
  holder: { person_id: "p1", name: "Hal", email: "hal@example.test" },
  candidates: [],
  hours_basis: true,
  ...over,
});

const report = (over: Partial<RebalanceReport> = {}): RebalanceReport => ({
  project_id: null,
  scope: "portfolio",
  horizon_days: 14,
  hr_visible: true,
  window: { starts_on: "2026-09-24", ends_on: "2026-10-07", days: 14 },
  ...over,
});

describe("the words", () => {
  it("names the holder and the due date, or says there is no date", () => {
    expect(holderLine(task())).toBe("Held by Hal · due 27 Sep 2026");
    expect(holderLine(task({ due_on: null }))).toBe("Held by Hal · no due date");
  });

  it("names three helpers at most, in the server's order", () => {
    const c = (name: string) => ({
      person_id: null, name, email: `${name}@x.in`, skill_points: 1,
      matched_skills: [], rank: 1,
    });
    expect(helpersLine(task({ candidates: [c("A"), c("B"), c("C"), c("D")] }))).toBe(
      "A, B, C"
    );
    expect(helpersLine(task())).toBeNull();
  });

  it("lists what an idle person could take", () => {
    expect(
      pickupLine({
        person_id: null, name: "Ivy", email: "ivy@x.in",
        tasks: [
          { task_id: "a", title: "Jig", project_name: null, kind: "unassigned",
            skill_points: 1, matched_skills: [] },
          { task_id: "b", title: "Frame", project_name: null, kind: "at_risk_help",
            skill_points: 1, matched_skills: [] },
        ],
      })
    ).toBe("Jig, Frame");
  });

  it("says what each cap cut, from the server's totals", () => {
    const data = report({ at_risk_total: 11, pickups_total: 25 });
    expect(rebalanceCapNote(data, 8, 20)).toBe(
      "Showing 8 of 11 tasks at risk. Showing 20 of 25 people who could take work."
    );
    expect(rebalanceCapNote(report({ at_risk_total: 2, pickups_total: 1 }), 2, 1)).toBeNull();
  });

  it("reads absent lists as absent, never as a crash", () => {
    expect(rebalanceTasks(report({ hr_visible: false }))).toEqual([]);
    expect(rebalancePickups(undefined)).toEqual([]);
    expect(REBALANCE_HR_HINT).toBe("Rebalancing needs HR read access. An admin can see it.");
  });
});

describe("the browser counts nothing", () => {
  const ARITHMETIC = /\.reduce\(|Math\.|\s[-*/]\s|\+=|-=|\+\+|--/;

  function code(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  }

  it("holds no arithmetic in the words", () => {
    const source = readFileSync(join(__dirname, "rebalance.ts"), "utf-8");
    expect(code(source)).not.toMatch(ARITHMETIC);
  });

  it("holds no arithmetic in the panel itself", () => {
    const source = readFileSync(
      join(__dirname, "..", "components", "AnalyticsPanels.tsx"),
      "utf-8"
    ).replace(/\r\n/g, "\n");
    const start = source.indexOf("export function RebalancePanel");
    const end = source.indexOf("\n}\n", start);
    expect(start).toBeGreaterThan(-1);
    expect(code(source.slice(start, end))).not.toMatch(ARITHMETIC);
  });

  it("would catch a sum if one arrived", () => {
    expect("const left = total - shown;").toMatch(ARITHMETIC);
  });
});
