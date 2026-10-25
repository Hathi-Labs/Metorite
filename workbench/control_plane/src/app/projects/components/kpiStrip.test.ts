/**
 * WS-27bn RA-2 — the KPI strip and "Who did the work", the markup half.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B and E, §8 RA-2. The
 * vitest environment has no DOM, so these tests render with
 * `renderToStaticMarkup`. The pure rules are `lib/kpiStrip.test.ts`.
 *
 * Done-when of RA-2: "each tile's rule is in its tooltip".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PreviewReportBody, WhoDidTheWorkReport } from "../lib/api";
import { TILE_RULES } from "../lib/kpiStrip";
import { WhoDidTheWorkPanel } from "./AnalyticsPanels";
import { RenderedBody } from "./ReportsView";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

/** `&` and quotes, as React escapes them in an attribute. */
const attr = (s: string) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;");

const WEEKS = [
  {
    week_start: "2026-10-12",
    agent_finished: 1,
    agent_assisted: 0,
    person: 2,
    agents: 1,
    people: 2,
    completed: 3,
  },
  {
    week_start: "2026-10-19",
    agent_finished: 2,
    agent_assisted: 1,
    person: 1,
    agents: 3,
    people: 1,
    completed: 4,
  },
];

function preview(scope: "portfolio" | "node", withKpis = true): PreviewReportBody {
  return {
    report: {
      id: null,
      project_id: scope === "node" ? "p1" : null,
      scope,
      name: "Overview",
      config: {
        weeks: 2,
        skip_current_week: false,
        include_subtree: true,
        sections: ["finished", "throughput"],
      },
    },
    period_start: "2026-10-12",
    period_end: "2026-10-25",
    sections: {
      finished: {
        projects: [{ project_id: "p1", name: "Rig", completed: 7, cancelled: 0 }],
        total_completed: 7,
        total_cancelled: 0,
        actor_split: { agent_finished: 3, agent_assisted: 1, person: 3 },
        actor_summary:
          "7 tasks finished in this period. Agents worked on 4, and people did 3. A person finished 1 of the agent tasks.",
        actor_series: WEEKS,
        agent_members: [
          {
            member: "ana@example.test",
            member_name: "Ana Rao",
            total: 4,
            agents: [
              {
                agent: "Claude Code",
                client_via: "mcp:claude.ai",
                agent_finished: 3,
                agent_assisted: 1,
                total: 4,
              },
            ],
          },
        ],
        agent_members_total: 1,
      },
      throughput: {
        series: [],
        completed: 7,
        median_hours: 20,
        measured: 5,
        p90_hours: 50,
        actor_cycle: {
          people: { completed: 3, assisted: 0, measured: 3, median_hours: 30, p90_hours: 50 },
          agents: { completed: 4, assisted: 1, measured: 2, median_hours: 4, p90_hours: 6 },
        },
        actor_summary: "At the median, agent tasks took 4 hours and tasks of people took 30 hours.",
      },
    },
    ...(withKpis
      ? {
          kpis: {
            runs: {
              on: true,
              working: 2,
              silent: 1,
              failed_today: 0,
              silent_after_minutes: 15,
              members: [],
            },
            merge: {
              days: 14,
              merged: 3,
              closed: 1,
              rate: 0.75,
              merged_before: 0,
              closed_before: 0,
              rate_before: null,
              trend: null,
            },
            time: {
              finished: 7,
              run_tasks: 2,
              median_hours: 1.5,
              total_hours: 3,
              basis: "run_start_to_end",
            },
            cost: {
              per_task: null,
              source: null,
              known_tasks: 0,
              finished: 7,
              share: 0,
              reason: "x",
            },
          },
        }
      : {}),
  } as PreviewReportBody;
}

describe("the KPI strip in the live Overview", () => {
  it("draws the tiles that the server sent, each with its rule as its tooltip", () => {
    const out = html(createElement(RenderedBody, { body: preview("portfolio"), layout: "grid" }));
    expect(out).toContain('aria-label="Key figures"');
    expect(out).toContain("Finished this week");
    expect(out).toContain("Agents working now");
    expect(out).toContain("Cost per finished task");
    expect(out).toContain("Unknown");
    expect(out).toContain(`title="${attr(TILE_RULES.finished)}"`);
    expect(out).toContain(`title="${attr(TILE_RULES.cost)}"`);
    expect(out).toContain(`title="${attr(TILE_RULES.merge(14))}"`);
    // The four report tiles give way to the strip.
    expect(out).not.toContain(">Overdue<");
  });

  it("is absent from a saved report, which keeps its four tiles", () => {
    const out = html(createElement(RenderedBody, { body: preview("portfolio"), layout: "column" }));
    expect(out).not.toContain('aria-label="Key figures"');
    expect(out).toContain("Median cycle time");
  });

  it("falls back to the four tiles for a body with no kpis block", () => {
    const out = html(
      createElement(RenderedBody, { body: preview("portfolio", false), layout: "grid" })
    );
    expect(out).not.toContain('aria-label="Key figures"');
  });
});

describe("Who did the work", () => {
  it("replaces What we finished in the Overview of one project", () => {
    const out = html(createElement(RenderedBody, { body: preview("node"), layout: "grid" }));
    expect(out).toContain("Who did the work");
    expect(out).not.toContain(">What we finished<");
  });

  it("keeps What we finished for the whole organization", () => {
    const out = html(createElement(RenderedBody, { body: preview("portfolio"), layout: "grid" }));
    expect(out).toContain("What we finished");
    expect(out).not.toContain(">Who did the work<");
  });

  it("opens with the server's sentence, and names each agent under its member", () => {
    const b = preview("node");
    const data: WhoDidTheWorkReport = {
      period_start: b.period_start,
      period_end: b.period_end,
      actor_summary: b.sections.finished!.actor_summary!,
      actor_series: WEEKS,
      actor_split: b.sections.finished!.actor_split!,
      agent_members: b.sections.finished!.agent_members!,
      current_week_partial: true,
    };
    const out = html(createElement(WhoDidTheWorkPanel, { data }));
    const sentence = out.indexOf("7 tasks finished in this period.");
    expect(sentence).toBeGreaterThan(-1);
    expect(sentence).toBeLessThan(out.indexOf("finished per week"));
    expect(out).toContain("Agent work, by the member it works for");
    expect(out).toContain("Claude Code: 3 finished, 1 helped");
    expect(out).toContain("Agent-assisted");
    expect(out).toContain("this week, so far");
  });

  it("How long it took opens with its sentence and splits people and agents", () => {
    const out = html(createElement(RenderedBody, { body: preview("node"), layout: "grid" }));
    const sentence = out.indexOf("At the median, agent tasks took 4 hours");
    expect(sentence).toBeGreaterThan(-1);
    expect(out).toContain("Cycle time of people and of agents");
    expect(out).toContain(">People<");
    expect(out).toContain(">Agents<");
  });
});
