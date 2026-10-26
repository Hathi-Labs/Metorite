/**
 * WS-27bn R5f repair round 2: the email and the download match the app.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R5f, round 2 items 5, 6,
 * 8 and 9.
 * - Each of the ten sections has a heading with its one name.
 * - Each clear section prints the same clear line as the app.
 * - The stuck section prints the blocked list, even with nothing overdue.
 * - A list the email cuts says "…and N more".
 * - No line names the HR permission.
 */
import { describe, expect, it } from "vitest";

import { CAPACITY_HR_HINT, CONFLICTS_HR_HINT, REBALANCE_HR_HINT } from "@/app/projects/lib/hrHints";
import { REPORT_SECTIONS, sectionName } from "@/app/projects/lib/reportBuilder";
import { SECTION_CLEAR_LINES } from "@/app/projects/lib/sectionEmpty";

import { type RenderedReport, reportDocument, reportLayout } from "./reportEmail";

const WINDOW = { starts_on: "2026-09-30", ends_on: "2026-10-13", days: 14, ignored_by: [] };

/** Every section, each one clear. */
const CLEAR = {
  finished: { projects: [], total_completed: 0, total_cancelled: 0 },
  throughput: { series: [{ week_start: "2026-09-21", completed: 0 }], median_hours: null, measured: 0 },
  outlook: { velocity: { verdict: "done", remaining_tasks: 0 }, plan: {} },
  load: { people: [], total_tasks: 0 },
  capacity: { people: [], total_tasks: 0, hr_visible: true, horizon_days: 14 },
  pulse: { today: "2026-09-30", stale_days: 14, hr_visible: true, people_total: 0, hidden_people: 0, help_note: "", rows: [] },
  stuck: { overdue: [], overdue_total: 0, stale: [{ band: "under_7d", n: 0 }], blocked: [], blocked_total: 0 },
  // WS-27bn RA-3. Nothing waits, and no question came in the period.
  waiting: {
    summary: "Nobody is waiting on a person.",
    agent_runs_seen: true,
    questions: [],
    questions_total: 0,
    reviews: [],
    reviews_total: 0,
    handoffs: [],
    handoffs_total: 0,
    answer_time: { asked: 0, answered: 0, unanswered: 0, median_hours: null, p90_hours: null },
    max_rows: 20,
  },
  // WS-27bn RA-4. No run ended in the window, and none is silent now.
  agent_runs: {
    summary: "No agent run ended in the last 14 days.",
    on: true,
    agent_runs_seen: true,
    days: 14,
    window_start: "2026-09-17",
    outcomes: { completed: 0, failed: 0, stopped: 0, abandoned: 0, released: 0, server_closed: 0 },
    ended_total: 0,
    outcomes_before: { completed: 0, failed: 0, stopped: 0, abandoned: 0, released: 0, server_closed: 0 },
    series: [],
    success: { rate: null, completed: 0, rated: 0, rate_before: null, rated_before: 0, trend: null },
    silent: [],
    silent_total: 0,
    silent_after_minutes: 15,
    sizes: { under_15m: 0, under_1h: 0, under_4h: 0, over_4h: 0, unmeasured: 0 },
    sized_total: 0,
    long_runs: [],
    long_total: 0,
    long_after_hours: 4,
    long_line: null,
    max_rows: 20,
  },
  // WS-27bn RA-5. No agent pull request, no reopen and no failed run.
  quality: {
    summary: "No agent pull request merged or closed in the last 14 days.",
    on: true,
    quality_seen: false,
    days: 14,
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
    checks: { failing_total: 0, failing: [], merged_red: 0, merged_red_before: 0 },
    rework: {
      reopened: {
        agents: { finished: 0, undone: 0, reopened: 0, finished_before: 0, undone_before: 0, reopened_before: 0, rate: null, rate_before: null, trend: null },
        people: { finished: 0, undone: 0, reopened: 0, finished_before: 0, undone_before: 0, reopened_before: 0, rate: null, rate_before: null, trend: null },
      },
      retried: { failed: 0, retried: 0, failed_before: 0, retried_before: 0, rate: null, rate_before: null, trend: null },
    },
    max_rows: 5,
  },
  hygiene: { open_total: 0, stale_days: 14, by_kind: {}, rows: [] },
  conflicts: { rows: [], total: 0, by_kind: {}, hr_visible: true, horizon_days: 14, window: WINDOW },
  rebalance: { hr_visible: true, at_risk: [], at_risk_total: 0, pickups: [], pickups_total: 0, idle_total: 0 },
};

function render(sections: Record<string, unknown>): RenderedReport {
  return {
    report: { name: "Weekly", scope: "portfolio" },
    period_start: "2026-09-21",
    period_end: "2026-09-27",
    sections,
  } as unknown as RenderedReport;
}

const layout = (sections: Record<string, unknown>, rows = 10) =>
  reportLayout(render(sections), rows, new Date(2026, 8, 30));

describe("item 8: each heading says the section's one name", () => {
  it.each(REPORT_SECTIONS.map((s) => s.key))("%s", (key) => {
    const { parts } = layout({ [key]: (CLEAR as Record<string, unknown>)[key] });
    expect(parts).toHaveLength(1);
    expect(parts[0].head.lead.startsWith(sectionName(key))).toBe(true);
  });
});

describe("item 5: a clear section prints the app's clear line", () => {
  it.each(REPORT_SECTIONS.map((s) => s.key))("%s", (key) => {
    const { parts } = layout({ [key]: (CLEAR as Record<string, unknown>)[key] });
    expect(parts[0].notes).toContain(SECTION_CLEAR_LINES[key]);
  });
});

describe("WS-27bn RA-5: the quality section prints the summary first, then the figures", () => {
  const busy = {
    ...(CLEAR as Record<string, any>).quality,
    summary:
      "8 agent pull requests merged and 2 closed with no merge in the last 14 days." +
      " The merge rate is 80%, up from 70% in the 14 days before.",
    prs: { merged: 8, closed: 2, rate: 0.8, merged_before: 7, closed_before: 3, rate_before: 0.7, trend: "up", open: 3 },
    checks: {
      failing_total: 1,
      failing: [
        {
          id: "t1", task_number: 31, title: "Fix the checks", project_id: "p1", project_name: "Rig",
          since: null, age_hours: 50, ref: "acme/rig#12", url: null, pr_title: null, pr_state: "open",
          agent: "Claude Code", client_via: null, member: null, member_name: "Ana Rao",
        },
      ],
      merged_red: 1,
      merged_red_before: 0,
    },
    rework: {
      reopened: {
        agents: { finished: 10, undone: 2, reopened: 3, finished_before: 8, undone_before: 1, reopened_before: 1, rate: 0.2, rate_before: 0.125, trend: "up" },
        people: { finished: 20, undone: 1, reopened: 1, finished_before: 18, undone_before: 2, reopened_before: 2, rate: 0.05, rate_before: 0.1111, trend: "down" },
      },
      retried: { failed: 5, retried: 3, failed_before: 4, retried_before: 1, rate: 0.6, rate_before: 0.25, trend: "up" },
    },
  };

  it("leads with the server's sentence, and states no goal", () => {
    const { parts } = layout({ quality: busy });
    expect(parts).toHaveLength(1);
    expect(parts[0].head.lead).toBe(`Quality: ${busy.summary}`);
    const text = parts[0].items.join("\n");
    expect(text).toContain("Merge rate 80%, up from 70% · 8 merged · 2 closed, not merged · 3 open now");
    expect(text).toContain("Reopened after agents: 2 of 10 finishes (20%) · 3 tasks reopened · before: 1 of 8");
    expect(text).toContain("Reopened after people: 1 of 20 finishes (5%) · 1 task reopened · before: 2 of 18");
    expect(text).toContain("3 of 5 failed runs got a new run");
    expect(text).toContain("Merged while red: 1");
    expect(text).toContain("#31 Fix the checks · Claude Code for Ana Rao · acme/rig#12");
    expect(text).not.toMatch(/\b(goal|target)\b/i);
  });
});

describe("item 5: the stuck section prints the blocked list", () => {
  const blocked = [
    { id: "b1", title: "Mount the Z-axis motor", task_number: 41, due_at: null },
    { id: "b2", title: "Flash firmware 3.1", task_number: 57, due_at: null },
  ];

  it("prints with blocked work and nothing overdue", () => {
    const { parts } = layout({
      stuck: { overdue: [], overdue_total: 0, stale: [{ band: "under_7d", n: 2 }], blocked, blocked_total: 2 },
    });
    expect(parts).toHaveLength(1);
    const text = [parts[0].head.lead, parts[0].head.extra, ...parts[0].items].join("\n");
    expect(text).toContain("2 blocked");
    expect(text).toContain("Blocked: #41 Mount the Z-axis motor");
    expect(text).toContain("Blocked: #57 Flash firmware 3.1");
    expect(parts[0].notes).not.toContain(SECTION_CLEAR_LINES.stuck);
  });

  it("says how many blocked tasks the list left out", () => {
    const { parts } = layout({
      stuck: { overdue: [], overdue_total: 0, stale: [], blocked, blocked_total: 9 },
    });
    expect(parts[0].notes).toContain("…and 7 more blocked tasks");
  });
});

describe("item 6: a list the email cuts says so", () => {
  const people = Array.from({ length: 4 }, (_, i) => ({
    assignee: `p${i}@x.io`,
    name: `P${i}`,
    kind: "person",
    open_tasks: 3,
    overdue: 1,
  }));

  it("load, capacity, conflicts and the overdue projects", () => {
    const { parts } = layout(
      {
        load: { people, total_tasks: 12, people_total: 4 },
        capacity: { people, total_tasks: 12, hr_visible: true, horizon_days: 14 },
        conflicts: {
          rows: people.map((_, i) => ({ kind: "blocker_late", severity: "high", sentence: `Row ${i}` })),
          total: 4,
          by_kind: { blocker_late: 4 },
          hr_visible: true,
          horizon_days: 14,
          window: WINDOW,
        },
        stuck: {
          overdue: people.map((p, i) => ({ project_id: `x${i}`, name: `Project ${i}`, overdue: 1 })),
          overdue_total: 4,
          stale: [],
        },
      },
      2
    );
    const notes = parts.flatMap((p) => p.notes);
    expect(notes).toContain("…and 2 more people");
    expect(notes).toContain("…and 2 more conflicts");
    expect(notes).toContain("…and 2 more projects");
    expect(notes.filter((n) => n === "…and 2 more people")).toHaveLength(2);
  });

  it("the server's cut of the Load rows is said too", () => {
    const { parts } = layout({ load: { people, total_tasks: 30, people_total: 25 } });
    expect(parts[0].notes).toContain("…and 21 more people");
  });

  it("the download keeps every row the render holds", () => {
    const many = Array.from({ length: 15 }, (_, i) => ({ ...people[0], assignee: `q${i}@x.io` }));
    const { markdown } = reportDocument(render({ load: { people: many, total_tasks: 15 } }));
    expect(markdown).toContain("q14@x.io");
    expect(markdown).not.toContain("more people");
  });
});

describe("items 2 and 9: the HR lines", () => {
  it("no email line names the permission", () => {
    const { parts } = layout({
      capacity: { ...CLEAR.capacity, hr_visible: false },
      conflicts: { ...CLEAR.conflicts, hr_visible: false },
      rebalance: { hr_visible: false },
    });
    const text = JSON.stringify(parts);
    expect(text).not.toMatch(/HR read access|Four kinds/);
    expect(text).toContain(CAPACITY_HR_HINT);
    expect(text).toContain(CONFLICTS_HR_HINT);
    expect(text).toContain(REBALANCE_HR_HINT);
  });

  it("no 'No conflicts' beside the line about the hidden kinds", () => {
    const { parts } = layout({ conflicts: { ...CLEAR.conflicts, hr_visible: false } });
    expect(parts[0].notes).toContain(CONFLICTS_HR_HINT);
    expect(parts[0].notes).not.toContain(SECTION_CLEAR_LINES.conflicts);
  });
});

describe("RA-4b: with agent runs off, no section says clear", () => {
  it("the email prints the server's summary and no clear line", () => {
    const off = "Agent runs are off for this organization, so no run is counted.";
    const { parts } = layout({
      agent_runs: { ...(CLEAR.agent_runs as object), on: false, summary: off },
    });
    expect(parts).toHaveLength(1);
    expect(parts[0].head.lead).toContain(off);
    expect(parts[0].notes).not.toContain(SECTION_CLEAR_LINES.agent_runs);
    const waiting = layout({
      waiting: { ...(CLEAR.waiting as object), on: false, summary: off },
    });
    expect(waiting.parts[0].head.lead).toContain(off);
    expect(waiting.parts[0].notes).not.toContain(SECTION_CLEAR_LINES.waiting);
  });
});
