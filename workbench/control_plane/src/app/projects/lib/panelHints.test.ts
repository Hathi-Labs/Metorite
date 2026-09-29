/**
 * The Reports UX pass (2026-09-29, `projects_reports.md` §6.5).
 *
 * §6.5 item 4: a section's panel and its builder checkbox say the same sentence,
 * from `panelHints.ts`. §6.5 item 17: the pulse note sits in the hint's tooltip,
 * and "Who is overloaded" shows a name when the payload has one.
 *
 * Rendered through `react-dom/server` in the node environment, as
 * `reportVisuals.test.ts` does.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LoadPanel, PulsePanel } from "../components/AnalyticsPanels";
import type { LoadReport, PulseReport } from "./api";
import { FINISHED_HINT_LEAD, PANEL_HINTS } from "./panelHints";
import { REPORT_SECTIONS } from "./reportBuilder";

const source = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
const panels = source("../components/AnalyticsPanels.tsx");
const view = source("../components/ReportsView.tsx");

describe("(4) one sentence for each section", () => {
  it("every section has a hint", () => {
    expect(Object.keys(PANEL_HINTS).sort()).toEqual(
      REPORT_SECTIONS.map((s) => s.key).sort()
    );
    for (const hint of Object.values(PANEL_HINTS)) expect(hint.length).toBeGreaterThan(10);
  });

  it("the panels read the hints and hold no copy", () => {
    for (const [key, hint] of Object.entries(PANEL_HINTS)) {
      if (key === "finished") continue;
      expect(panels).toContain(`PANEL_HINTS.${key}`);
      expect(panels).not.toContain(`"${hint}"`);
    }
    expect(panels).toContain("${FINISHED_HINT_LEAD}, ${period(");
    expect(PANEL_HINTS.finished.startsWith(FINISHED_HINT_LEAD)).toBe(true);
  });

  it("the builder's section tiles take their line from the same hints", () => {
    // R5f round 1 (§6.6 C). The tile prints the short hint, and its
    // tooltip is the longer detail.
    expect(view).toMatch(/PANEL_HINTS\[sectionKey\]/);
    expect(view).toMatch(/PANEL_HINT_DETAILS\[sectionKey\]/);
  });
});

describe("(17) the panels in a report", () => {
  it("pulse puts its note in the hint's tooltip, not in a line of its own", () => {
    const note = "Behind two runs in a row is not checked yet.";
    const data = {
      today: "2026-09-29",
      stale_days: 7,
      hr_visible: true,
      people_total: 0,
      hidden_people: 0,
      help_note: note,
      rows: [],
    } as unknown as PulseReport;
    const html = renderToStaticMarkup(createElement(PulsePanel, { data }));
    expect(html).toContain(`title="${note}"`);
    expect(html).not.toContain(`>${note}<`);
  });

  it("Who is overloaded shows the name when the payload has one", () => {
    const data = {
      project_id: null,
      scope: "portfolio",
      total_tasks: 3,
      people: [
        { assignee: "meera@example.test", name: "Meera Iyer", open_tasks: 2, overdue: 0 },
        { assignee: "noa@example.test", open_tasks: 1, overdue: 0 },
      ],
    } as unknown as LoadReport;
    const html = renderToStaticMarkup(createElement(LoadPanel, { data }));
    expect(html).toContain(">Meera Iyer<");
    expect(html).not.toContain(">meera@example.test<");
    // No name in the payload: the address, as before.
    expect(html).toContain(">noa@example.test<");
  });
});
