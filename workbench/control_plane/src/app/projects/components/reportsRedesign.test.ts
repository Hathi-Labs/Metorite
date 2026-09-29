/**
 * WS-27bn R5f repair round 1: the calm builder and report.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.6 and §8 R5f round 1,
 * done-when (m), (n) and (o). The vitest environment has no DOM, so these
 * tests render the parts with `renderToStaticMarkup`, and they pin the pure
 * rules. No test can run an effect: the remount and link cases are pinned by
 * the pure rule and by the props the pane passes.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { reportLayout } from "@/lib/reportEmail";

import type { RenderedReportBody } from "../lib/api";
import {
  MAX_REPORT_NAME,
  REPORT_SECTIONS,
  linkPending,
  previewNeeded,
  reportsPane,
} from "../lib/reportBuilder";
import { SECTION_CLEAR_LINES, allClear, clearNote, sectionIsClear } from "../lib/sectionEmpty";
import { SECTION_ICONS } from "../lib/sectionIcons";
import { idleLine } from "./AnalyticsPanels";
import {
  AllClear,
  BuilderHeader,
  ClearRow,
  HeaderMeta,
  RenderedBody,
  SectionTile,
  TitleField,
} from "./ReportsView";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const VIEW = readFileSync(join(__dirname, "ReportsView.tsx"), "utf-8");

const PULSE_CLEAR = {
  today: "2026-09-30",
  stale_days: 14,
  hr_visible: true,
  people_total: 0,
  hidden_people: 0,
  help_note: "",
  rows: [],
};
const CONFLICTS_CLEAR = {
  rows: [],
  total: 0,
  by_kind: {},
  hr_visible: true,
  horizon_days: 14,
  window: { starts_on: "2026-09-30", ends_on: "2026-10-13", days: 14, ignored_by: [] },
};
const REBALANCE_CLEAR = {
  project_id: null,
  scope: "portfolio",
  include_subtree: true,
  horizon_days: 14,
  hr_visible: true,
  window: { starts_on: "2026-09-30", ends_on: "2026-10-13", days: 14 },
  at_risk: [],
  at_risk_total: 0,
  pickups: [],
  pickups_total: 0,
  idle_total: 0,
};

function teamPulse(sections: Record<string, unknown>): RenderedReportBody {
  return {
    report: {
      id: "r-1",
      project_id: null,
      scope: "portfolio",
      name: "Team pulse",
      config: {
        weeks: 1,
        skip_current_week: false,
        include_subtree: true,
        sections: Object.keys(sections),
      },
      created_by: "a@x.io",
      created_at: "2026-09-30T00:00:00Z",
    },
    period_start: "2026-09-29",
    period_end: "2026-09-30",
    sections,
  } as unknown as RenderedReportBody;
}

describe("one icon for each section, in one map", () => {
  it("names every section of REPORT_SECTIONS, and nothing else", () => {
    expect(Object.keys(SECTION_ICONS).sort()).toEqual(REPORT_SECTIONS.map((s) => s.key).sort());
  });

  it("each section has a friendly clear line", () => {
    expect(Object.keys(SECTION_CLEAR_LINES).sort()).toEqual(
      REPORT_SECTIONS.map((s) => s.key).sort()
    );
    for (const line of Object.values(SECTION_CLEAR_LINES)) {
      expect(line).not.toMatch(/;/);
      expect(line.split(/\s+/).length).toBeLessThanOrEqual(10);
    }
  });
});

describe("(o) the all-clear state: the owner's Team pulse", () => {
  const body = teamPulse({
    pulse: PULSE_CLEAR,
    conflicts: CONFLICTS_CLEAR,
    rebalance: REBALANCE_CLEAR,
  });
  const markup = html(createElement(RenderedBody, { body, headerLine: "Whole organization · As of 30 Sep 2026" }));

  it("draws one calm card, not three empty panels", () => {
    expect(markup).toContain("All clear");
    expect(markup).toContain("Nothing needs attention in the whole organization right now.");
    expect(markup.match(/<section[^>]*aria-labelledby=/g) ?? []).toHaveLength(0);
  });

  it("lists each section with its friendly line", () => {
    expect(markup).toContain("No open work for anyone here right now.");
    expect(markup).toContain("No conflicts. The plan lines up.");
    expect(markup).toContain("Nothing is at risk, and nobody is idle.");
  });

  it("drops the old words", () => {
    for (const old of [
      "No card to show",
      "No task at risk and nobody idle",
      "Dated kinds",
      "Dependencies are checked whenever they fall",
      "Nothing is rescheduled",
      "Nothing is reassigned",
    ]) {
      expect(markup, old).not.toContain(old);
    }
  });

  it("is never all clear while the server hid rows from the reader", () => {
    expect(sectionIsClear("pulse", { ...PULSE_CLEAR, hidden_people: 2 })).toBe(false);
    expect(sectionIsClear("conflicts", { ...CONFLICTS_CLEAR, hidden_people: 1 })).toBe(false);
    expect(sectionIsClear("rebalance", { ...REBALANCE_CLEAR, hr_visible: false })).toBe(false);
    expect(sectionIsClear("stuck", { overdue: [], overdue_total: 0 })).toBe(false);
    expect(sectionIsClear("unknown", {})).toBe(false);
  });

  it("an empty body is not all clear", () => {
    expect(allClear({}).clear).toBe(false);
  });

  it("one clear section among others is a compact row", () => {
    const mixed = html(
      createElement(RenderedBody, {
        body: teamPulse({
          conflicts: CONFLICTS_CLEAR,
          stuck: {
            overdue: [{ project_id: "p", name: "Printer X2", overdue: 2 }],
            overdue_total: 2,
            stale: [{ band: "under_7d", n: 3 }],
          },
        }),
      })
    );
    expect(mixed).not.toContain("All clear");
    expect(mixed).toContain("Where the plan conflicts");
    expect(mixed).toContain("No conflicts. The plan lines up.");
    expect(mixed.match(/<section[^>]*aria-labelledby=/g)).toHaveLength(1);
  });

  it("the email says the same clear line", () => {
    const layout = reportLayout(body as never, 10, new Date(2026, 8, 30));
    const notes = layout.parts.flatMap((p) => p.notes);
    expect(notes).toContain("No conflicts. The plan lines up.");
    expect(notes).toContain("No open work for anyone here right now.");
    expect(clearNote("conflicts", { ...CONFLICTS_CLEAR, total: 1, rows: [{}] })).toEqual([]);
  });
});

describe("(o) the header row", () => {
  it("draws back, the title and the actions in one row", () => {
    const markup = html(
      createElement(BuilderHeader, {
        onBack: () => {},
        title: createElement(TitleField, { value: "Team pulse", onChange: () => {} }),
        actions: createElement("button", null, "Save report"),
      })
    );
    expect(markup).toContain(">Reports<");
    expect(markup).toContain('value="Team pulse"');
    expect(markup).toContain("Save report");
  });

  it("the title is a labelled input with the name limit", () => {
    const markup = html(createElement(TitleField, { value: "x", onChange: () => {} }));
    expect(markup).toContain("Report name");
    expect(markup).toContain(`maxLength="${MAX_REPORT_NAME}"`);
    expect(markup).toContain("reveal-on-hover");
  });

  it("the pane has no second heading, and the builder no Name block", () => {
    expect(VIEW).not.toMatch(/<h2[^>]*>Reports<\/h2>/);
    expect(VIEW).not.toMatch(/>Name<\/span>/);
    expect(VIEW).not.toContain("Started from a template");
    expect(VIEW).not.toContain("Preview. The server computes each number");
    expect(VIEW).toContain("From template: {template.name}");
  });
});

describe("(o) the section tiles", () => {
  const tile = (props: Partial<Parameters<typeof SectionTile>[0]>) =>
    html(
      createElement(SectionTile, {
        sectionKey: "load",
        label: "Open work",
        on: false,
        blocked: false,
        last: false,
        onToggle: () => {},
        ...props,
      })
    );

  it("a chosen tile is pressed, tinted and checked", () => {
    const on = tile({ on: true });
    expect(on).toContain('aria-pressed="true"');
    expect(on).toContain("bg-primary/10");
    expect(on).not.toMatch(/invisible/);
  });

  it("a tile that is off is not pressed, and its check is hidden", () => {
    const off = tile({ on: false });
    expect(off).toContain('aria-pressed="false"');
    expect(off).toMatch(/invisible/);
    expect(off).toContain("Open tasks per person, by due date.");
  });

  it("a blocked tile is disabled and says why", () => {
    const blocked = tile({ blocked: true, sectionKey: "outlook", label: "Forecast" });
    expect(blocked).toContain("disabled");
    expect(blocked).toContain("Off for a person or team.");
  });

  it("the builder draws tiles, not a checkbox grid of sections", () => {
    expect(VIEW).toContain("<SectionTile");
    expect(VIEW).not.toMatch(/<Checkbox[^>]*checked=\{on\}/);
  });
});

describe("the clear row and the header meta", () => {
  it("a clear row has the check, the title and one line", () => {
    const row = html(createElement(ClearRow, { sectionKey: "stuck", title: "Overdue" }));
    expect(row).toContain("Overdue");
    expect(row).toContain("Nothing is stuck.");
  });

  it("the all-clear card names a project scope in words", () => {
    const card = html(createElement(AllClear, { keys: ["pulse"], scope: "In Printer X2" }));
    expect(card).toContain("Nothing needs attention in Printer X2 right now.");
  });

  it("the meta line keeps each part of reportHeaderLine", () => {
    const meta = html(createElement(HeaderMeta, { line: "About Meera · In Printer X2 · As of 30 Sep 2026" }));
    for (const part of ["About Meera", "In Printer X2", "As of 30 Sep 2026"]) {
      expect(meta).toContain(part);
    }
  });
});

describe("(m) a return to Home asks for no second preview", () => {
  it("the preview on screen answers the same key", () => {
    expect(previewNeeded("k1", "k1", false)).toBe(false);
    expect(previewNeeded("k1", "k2", false)).toBe(true);
    expect(previewNeeded(null, "k1", false)).toBe(true);
    expect(previewNeeded(null, "k1", true)).toBe(false);
  });

  it("Home keeps the Overview choices and its last preview", () => {
    expect(VIEW).toContain("initialPreview={overviewPreview}");
    expect(VIEW).toContain("onDraft={setOverviewDraft}");
    expect(VIEW).toContain("initial={overviewDraft}");
    expect(VIEW).toContain("if (!previewNeeded(shownKey.current, previewKey, blocked)) return;");
  });
});

describe("(n) a builder link skips Overview", () => {
  it("waits while the address holds a link key", () => {
    expect(reportsPane({ building: false, selected: null, linkPending: true })).toBe(
      "wait-for-link"
    );
    expect(reportsPane({ building: false, selected: null, linkPending: false })).toBe("overview");
    expect(reportsPane({ building: true, selected: null, linkPending: true })).toBe("builder");
    expect(reportsPane({ building: false, selected: "r-1", linkPending: false })).toBe("saved");
  });

  it("knows the link keys", () => {
    expect(linkPending(new URLSearchParams("template=team_pulse"))).toBe(true);
    expect(linkPending(new URLSearchParams("report_node=x"))).toBe(true);
    expect(linkPending(new URLSearchParams("task=1"))).toBe(false);
  });

  it("the pane mounts Overview only when reportsPane says overview", () => {
    expect(VIEW).toMatch(/shown === "overview" \? \(/);
    expect(VIEW).toMatch(/shown === "wait-for-link" \? \(/);
  });
});

describe("the rebalance line tells the truth about idle people", () => {
  it("says nobody is idle only when nobody is", () => {
    expect(idleLine(0)).toBeNull();
    expect(idleLine(undefined)).toBeNull();
    expect(idleLine(1)).toBe("Nothing is at risk. 1 idle person has no task that fits.");
    expect(idleLine(3)).toContain("3 idle people");
  });

  it("a section with an idle person is not all clear", () => {
    expect(sectionIsClear("rebalance", { ...REBALANCE_CLEAR, idle_total: 1 })).toBe(false);
    const markup = html(createElement(RenderedBody, { body: teamPulse({ rebalance: { ...REBALANCE_CLEAR, idle_total: 1 } }) }));
    expect(markup).not.toContain("nobody is idle");
    expect(markup).toContain("1 idle person has no task that fits.");
  });
});
