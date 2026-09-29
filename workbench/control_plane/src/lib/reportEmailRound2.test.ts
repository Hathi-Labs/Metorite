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
