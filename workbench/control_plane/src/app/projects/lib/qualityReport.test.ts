/**
 * WS-27bn RA-5 — "Quality", the pure rules of the client.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B, E and F, §8 RA-5.
 * The server decides every figure (`analytics_quality.py`). This file holds
 * the words, the explicit hues, the clear rule, the hide rule that the
 * Overview reads, and the scan that refuses a 100% goal. The markup half is
 * `components/qualityPanel.test.ts`.
 */
import { describe, expect, it } from "vitest";

import { accentForSlot } from "@/lib/categorical";

import type { QualityReport, RenderedReportBody } from "./api";
import { overviewShown } from "./overviewLive";
import {
  PR_SEGMENTS,
  QUALITY_RULES,
  REWORK_GROUPS,
  failingLines,
  mergeFigure,
  mergeTrend,
  percentWords,
  qualityAccent,
  qualityCopy,
  qualityHidden,
  qualityIsClear,
  qualityRowCount,
  reopenedBefore,
  reopenedFigure,
  reopenedRate,
  reopenedTasks,
  retriedLine,
} from "./qualityReport";
import { NO_SUBJECT_SECTIONS, OVERVIEW_SECTIONS, PERIOD_FREE_SECTIONS } from "./reportBuilder";
import { SECTION_CLEAR_LINES, sectionIsClear } from "./sectionEmpty";

const GROUP = {
  finished: 10,
  undone: 2,
  reopened: 3,
  finished_before: 8,
  undone_before: 1,
  reopened_before: 1,
  rate: 0.2,
  rate_before: 0.125,
  trend: "up" as const,
};

function section(over: Partial<QualityReport> = {}): QualityReport {
  return {
    summary: "8 agent pull requests merged and 2 closed with no merge in the last 14 days.",
    on: true,
    quality_seen: true,
    days: 14,
    prs: {
      merged: 8,
      closed: 2,
      rate: 0.8,
      merged_before: 7,
      closed_before: 3,
      rate_before: 0.7,
      trend: "up",
      open: 3,
    },
    checks: { failing_total: 0, failing: [], merged_red: 0, merged_red_before: 0 },
    rework: {
      reopened: { agents: GROUP, people: { ...GROUP, finished: 20, undone: 1, reopened: 1, undone_before: 2, rate: 0.05 } },
      retried: {
        failed: 5,
        retried: 3,
        failed_before: 4,
        retried_before: 1,
        rate: 0.6,
        rate_before: 0.25,
        trend: "up",
      },
    },
    max_rows: 5,
    ...over,
  };
}

/** A scope with nothing in it. */
function quiet(over: Partial<QualityReport> = {}): QualityReport {
  return section({
    quality_seen: false,
    prs: {
      merged: 0,
      closed: 0,
      rate: null,
      merged_before: 0,
      closed_before: 0,
      rate_before: null,
      trend: null,
      open: 0,
    },
    rework: {
      reopened: {
        agents: { ...GROUP, finished: 0, undone: 0, reopened: 0, undone_before: 0, rate: null, rate_before: null, trend: null },
        people: { ...GROUP, finished: 0, undone: 0, reopened: 0, undone_before: 0, rate: null, rate_before: null, trend: null },
      },
      retried: {
        failed: 0,
        retried: 0,
        failed_before: 0,
        retried_before: 0,
        rate: null,
        rate_before: null,
        trend: null,
      },
    },
    ...over,
  });
}

const FAIL_ROW = {
  id: "t1",
  task_number: 31,
  title: "Fix the checks",
  project_id: "p1",
  project_name: "Rig",
  since: null,
  age_hours: 50,
  ref: "acme/rig#12",
  url: null,
  pr_title: null,
  pr_state: "open",
  agent: "Claude Code",
  client_via: "mcp:claude.ai:claude-code",
  member: "ana@example.test",
  member_name: "Ana Rao",
};

describe("the merge rate", () => {
  it("is a whole percent, or words when no pull request closed", () => {
    expect(percentWords(0.8)).toBe("80%");
    expect(mergeFigure(section())).toBe("80%");
    expect(mergeFigure(quiet())).toBe("No rate yet");
  });

  it("says its trend in words, with no target (§6.8 E4)", () => {
    expect(mergeTrend(section())).toBe("up from 70%");
    expect(
      mergeTrend(section({ prs: { ...section().prs, trend: "down", rate_before: 0.9 } }))
    ).toBe("down from 90%");
    expect(mergeTrend(section({ prs: { ...section().prs, trend: "flat" } }))).toBe(
      "same as the 14 days before"
    );
    expect(mergeTrend(section({ prs: { ...section().prs, trend: null, rate_before: null } }))).toBeNull();
    expect(mergeTrend(quiet())).toBeNull();
  });

  it("names its rule: an open pull request does not count, and the strip shows the same rate", () => {
    expect(QUALITY_RULES.merge(14)).toContain("An open or draft pull request does not count");
    expect(QUALITY_RULES.merge(14)).toContain("same rate");
  });
});

describe("rework: two groups, never a person (§6.8 E1)", () => {
  it("has the agents group and the people group, in the server's order", () => {
    expect(REWORK_GROUPS.map((g) => g.key)).toEqual(["agents", "people"]);
  });

  it("takes the hue that RA-2 gives the group, as an explicit slot", () => {
    expect(qualityAccent("agents")).toBe(accentForSlot(3));
    expect(qualityAccent("people")).toBe(accentForSlot(0));
    const slots = (["merged", "closed", "open", "agents", "people"] as const).map(
      (k) => qualityAccent(k).dot
    );
    expect(new Set(slots).size).toBe(5);
  });

  it("says the reopened tasks over the finished ones, with the rate and the window before", () => {
    expect(reopenedFigure(GROUP)).toBe("2 of 10");
    expect(reopenedTasks(GROUP)).toBe("3 tasks reopened");
    expect(reopenedTasks({ reopened: 1 })).toBe("1 task reopened");
    expect(reopenedRate(GROUP)).toBe("20%");
    expect(reopenedRate({ rate: null })).toBe("No finish yet");
    expect(reopenedBefore(GROUP)).toBe("before: 1 of 8");
  });

  it("names the rule: a move to cancelled is not a reopen, and the group is the finish's", () => {
    expect(QUALITY_RULES.reopened(14)).toContain("A move to cancelled is not a reopen");
    expect(QUALITY_RULES.reopened(14)).toContain("agents (agent-finished or agent-assisted) or people");
  });

  it("says what is counted: each task once, and a cohort of the finishes that never passes 100%", () => {
    const rule = QUALITY_RULES.reopened(14);
    expect(rule).toContain("counts each task once");
    expect(rule).toContain("cohort");
    expect(rule).toContain("never passes 100%");
    // A recent finish has had less time to be reopened, so the newest days read low.
    expect(rule).toContain("less time to be reopened");
  });

  it("says the retried runs, and says so when runs are off", () => {
    expect(retriedLine(section())).toBe("3 of 5 failed runs got a new run");
    expect(retriedLine(quiet())).toBe("No run failed");
    expect(retriedLine(section({ on: false }))).toBe("Agent runs are off, so no retry is counted");
    const one = section();
    one.rework.retried = { ...one.rework.retried, failed: 1, retried: 1 };
    expect(retriedLine(one)).toBe("1 of 1 failed run got a new run");
  });
});

describe("the failing pull requests: the agent names its member (§6.8 E2)", () => {
  const withRows = section({
    checks: {
      failing_total: 7,
      failing: [FAIL_ROW, { ...FAIL_ROW, id: "t2", ref: "acme/rig#13", member_name: null, member: null }],
      merged_red: 0,
      merged_red_before: 0,
    },
  });

  it("keeps the server's order and names the agent for its member", () => {
    const lines = failingLines(withRows);
    expect(lines.map((l) => l.id)).toEqual(["t1", "t2"]);
    expect(lines[0].label).toBe("#31 Fix the checks");
    expect(lines[0].detail).toBe("Claude Code for Ana Rao · acme/rig#12 · linked 2 days ago");
    expect(lines[1].detail).toContain("Claude Code · acme/rig#13");
  });

  it("counts the rows the table names, not the server's total", () => {
    expect(qualityRowCount(withRows)).toBe(2);
    expect(withRows.checks.failing_total).toBe(7);
  });

  it("reads a missing list as no rows", () => {
    expect(failingLines({ checks: undefined as unknown as QualityReport["checks"] })).toEqual([]);
  });
});

describe("the clear rule and the hide rule (the done-when of RA-5)", () => {
  it("is clear only when nothing is in the window", () => {
    expect(qualityIsClear(quiet())).toBe(true);
    expect(qualityIsClear(section())).toBe(false);
    expect(qualityIsClear(undefined)).toBe(false);
    expect(sectionIsClear("quality", quiet())).toBe(true);
    expect(SECTION_CLEAR_LINES.quality).toBe(
      "No agent pull request, reopen or failed run."
    );
  });

  it("is not clear for any one of the five things", () => {
    const base = quiet();
    expect(qualityIsClear({ ...base, prs: { ...base.prs, merged: 1 } })).toBe(false);
    expect(qualityIsClear({ ...base, prs: { ...base.prs, closed: 1 } })).toBe(false);
    expect(qualityIsClear({ ...base, prs: { ...base.prs, open: 1 } })).toBe(false);
    expect(qualityIsClear({ ...base, checks: { ...base.checks, failing_total: 1 } })).toBe(false);
    expect(qualityIsClear({ ...base, checks: { ...base.checks, merged_red: 1 } })).toBe(false);
    const reopened = quiet();
    reopened.rework.reopened.people.reopened = 1;
    expect(qualityIsClear(reopened)).toBe(false);
    const failed = quiet();
    failed.rework.retried.failed = 1;
    expect(qualityIsClear(failed)).toBe(false);
  });

  it("is never clear while the runs are off, because a failed run cannot show", () => {
    expect(qualityIsClear(quiet({ on: false }))).toBe(false);
    expect(qualityHidden(quiet({ on: false }))).toBe(false);
  });

  it("hides only a clear section of a scope with nothing seen", () => {
    expect(qualityHidden(quiet())).toBe(true);
    expect(qualityHidden(quiet({ quality_seen: true }))).toBe(false);
    expect(qualityHidden(section({ quality_seen: false }))).toBe(false);
    expect(qualityHidden(undefined)).toBe(false);
  });

  it("the Overview drops it, as it drops waiting, and keeps a body of hidden panels alone", () => {
    const stuck = { overdue: [], overdue_total: 0 };
    const both = { quality: quiet(), stuck } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(both))).toEqual(["stuck"]);
    const alone = { quality: quiet() } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(alone))).toEqual(["quality"]);
    const busy = { quality: section(), stuck } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(busy)).sort()).toEqual(["quality", "stuck"]);
  });

  it("is on the Overview, refuses a subject, and ignores the report period", () => {
    expect(OVERVIEW_SECTIONS).toContain("quality");
    expect(NO_SUBJECT_SECTIONS).toContain("quality");
    expect(PERIOD_FREE_SECTIONS).toContain("quality");
  });
});

describe("no text calls 100% the goal (§6.8 E4)", () => {
  const GOAL = /\b(goal|goals|target|targets|aim|aims|should|ideal|perfect|best|worst|top)\b/i;
  const HUNDRED = /100\s*%\s*(is|as|means|goal|target)|(goal|target|aim|reach|hit)\w*\s+(of\s+)?100\s*%/i;

  it("scans every string of the module, the rules and the segments", () => {
    const copy = qualityCopy();
    expect(copy.length).toBeGreaterThan(10);
    for (const text of copy) {
      expect(text, text).not.toMatch(GOAL);
      expect(text, text).not.toMatch(HUNDRED);
    }
  });

  it("scans the words made from a rate of 100%", () => {
    const full = section({ prs: { ...section().prs, rate: 1, rate_before: 1, trend: "flat" } });
    const words = [mergeFigure(full), mergeTrend(full) ?? "", reopenedRate({ rate: 1 })];
    expect(words[0]).toBe("100%");
    for (const w of words) {
      expect(w).not.toMatch(GOAL);
      expect(w).not.toMatch(HUNDRED);
    }
  });

  it("the scan catches the phrases it is there to refuse", () => {
    for (const bad of ["Aim for 100% merged.", "The goal is 100%.", "100% is the target"]) {
      expect(GOAL.test(bad) || HUNDRED.test(bad), bad).toBe(true);
    }
  });

  it("names the three outcomes of a pull request", () => {
    expect(PR_SEGMENTS.map((s) => s.key)).toEqual(["merged", "closed", "open"]);
  });
});
