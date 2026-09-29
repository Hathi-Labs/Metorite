/**
 * The Reports UX pass (2026-09-29, `projects_reports.md` §6.5), rendered.
 *
 * The rules that a pure function holds are in `lib/reportBuilderUx.test.ts`.
 * This file renders the pieces of `ReportsView.tsx` that carry a rule of
 * their own: one Retry (item 8), the render error inside the pane (item 3),
 * the header line (item 4) and the gallery (item 5).
 *
 * Rendered through `react-dom/server` in the node environment, as
 * `reportVisuals.test.ts` does. That proves the markup, not the layout.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RenderedReportBody, ReportRow, ReportTemplate } from "../lib/api";
import { asOfDay, subjectPrompt } from "../lib/reportBuilder";
import {
  PreviewPrompt,
  RenderFailed,
  RenderedBody,
  ReportsHome,
  SubjectChipFailed,
} from "./ReportsView";

const VIEW = readFileSync(join(__dirname, "ReportsView.tsx"), "utf-8");

const T2: ReportTemplate = {
  key: "my_day",
  name: "My day",
  question: "What do I work on today?",
  scope_kinds: ["person"],
  available: true,
  sections: ["pulse"],
  weeks: 1,
  skip_current_week: false,
  requires_subject: "self",
};

const LIVE: ReportTemplate = {
  key: "weekly_delivery",
  name: "Weekly delivery",
  question: "What did we finish last week, and how fast?",
  scope_kinds: ["project", "org"],
  available: true,
  sections: ["finished"],
};

const SOON: ReportTemplate = {
  key: "exceptions",
  name: "Exceptions",
  question: "What is wrong right now?",
  scope_kinds: ["org"],
  available: false,
  waits_for: "A today period",
};

function row(over: Partial<ReportRow> = {}): ReportRow {
  return {
    id: "r1",
    project_id: null,
    scope: "portfolio",
    name: "Friday",
    config: { weeks: 1, skip_current_week: true, include_subtree: true, sections: ["finished"] },
    created_by: "a@example.test",
    created_at: "2026-09-28T00:00:00Z",
    ...over,
  };
}

const html = (el: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(el);

describe("(8) one Retry for a failed subjects read", () => {
  it("the chip keeps its shape and carries the one Retry, the line carries none", () => {
    const chip = html(createElement(SubjectChipFailed, { onRetry: () => {} }));
    const line = html(
      createElement(PreviewPrompt, {
        icon: "User",
        text: subjectPrompt({ subject: null }, T2, true) ?? "",
      })
    );
    expect(`${chip}${line}`.match(/>Retry</g)?.length).toBe(1);
    // A disabled chip that says what failed, not a bare sentence.
    expect(chip).toContain("disabled");
    expect(chip).toContain("People did not load");
    expect(line).toContain("did not load");
  });

  it("the view draws Retry in one place only", () => {
    expect(VIEW.match(/>\s*Retry\s*</g)?.length).toBe(1);
  });
});

describe("(3) a failed render stays inside the pane", () => {
  it("says what failed, offers Try again, and the way home", () => {
    const markup = html(
      createElement(RenderFailed, {
        error: "That report could not be rendered.",
        row: row({ can_edit: true }),
        onRetry: () => {},
        onHome: () => {},
        onEdit: () => {},
      })
    );
    expect(markup).toContain("That report could not be rendered.");
    expect(markup).toContain("Try again");
    expect(markup).toContain("Home");
    expect(markup).toContain("Edit");
    expect(markup).toContain('role="alert"');
  });

  it("Edit follows can_edit there too", () => {
    const markup = html(
      createElement(RenderFailed, {
        error: "x",
        row: row({ can_edit: false }),
        onRetry: () => {},
        onHome: () => {},
        onEdit: () => {},
      })
    );
    expect(markup).not.toContain("Edit");
  });

  it("the page has no error line above the pane", () => {
    expect(VIEW).not.toMatch(/\{error && \(\s*<p/);
  });
});

describe("(4) the header line", () => {
  const body = {
    report: row(),
    period_start: "2026-08-31",
    period_end: "2026-09-27",
    sections: {},
  } as unknown as RenderedReportBody;

  it("draws the line the caller built", () => {
    const line = "About Meera Iyer · In Printer X2 · 31 Aug – 27 Sep 2026";
    expect(html(createElement(RenderedBody, { body, headerLine: line }))).toContain(line);
  });

  it("says Whole organization, never Every space you can see", () => {
    const markup = html(createElement(RenderedBody, { body }));
    expect(markup).toContain("Whole organization");
    expect(VIEW).not.toContain("Every space you can see");
  });

  it("the as-of day is the server's when it sends one", () => {
    expect(asOfDay("2026-09-29", new Date(2026, 0, 5))).toBe("2026-09-29");
    expect(asOfDay(undefined, new Date(2026, 0, 5))).toBe("2026-01-05");
  });
});

describe("(5) the gallery", () => {
  const markup = html(
    createElement(ReportsHome, {
      rows: [],
      templates: [SOON, LIVE],
      templatesError: null,
      cardLine: () => "",
      onOpen: () => {},
      onStart: () => {},
    })
  );

  it("shows the live templates first, and folds the coming-soon ones", () => {
    expect(markup).toContain("Weekly delivery");
    expect(markup).toContain("Coming later (1)");
    expect(markup.indexOf("Weekly delivery")).toBeLessThan(markup.indexOf("Coming later"));
    // Folded: the coming-soon card is not drawn until the member opens it.
    expect(markup).not.toContain("What is wrong right now?");
  });

  it("the empty state is one line, with no row of buttons", () => {
    expect(markup).toContain("You have not saved a report yet. Pick a question below.");
    expect(markup).not.toContain("Start with:");
  });

  it("a coming-soon card names what it waits for in its tooltip only", () => {
    expect(VIEW).not.toMatch(/>\s*Waits for:/);
    expect(VIEW).toContain("title={template.waits_for ? `Waits for: ${template.waits_for}`");
  });
});

describe("(14) text follows the member's density", () => {
  it("ReportsView holds no px text size", () => {
    expect(VIEW).not.toMatch(/text-\[\d+px\]/);
  });
});
