/**
 * WS-27bn R5d — Edit follows the server's change rule (§9 Q13).
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R5d, done-when (ab).
 * The server computes `can_edit` for the author and for an admin. A reader
 * who may open the report and may not change it sees no Edit at all. The
 * control is absent, never disabled.
 *
 * Rendered through `react-dom/server` in the node environment, as
 * `reportVisuals.test.ts` does.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ReportRow } from "../lib/api";
import { ReportActions } from "./ReportsView";

function row(over: Partial<ReportRow>): ReportRow {
  return {
    id: "r1",
    project_id: null,
    scope: "portfolio",
    name: "Weekly",
    config: {
      weeks: 1,
      skip_current_week: true,
      include_subtree: true,
      sections: ["load"],
    },
    created_by: "m@example.test",
    created_at: "2026-09-28T00:00:00Z",
    ...over,
  };
}

function markup(r: ReportRow): string {
  return renderToStaticMarkup(
    createElement(ReportActions, { row: r, onHome: () => {}, onEdit: () => {} })
  );
}

describe("ReportActions — Edit follows can_edit", () => {
  it("shows Edit to the author", () => {
    expect(markup(row({ mine: true, can_edit: true }))).toContain("Edit");
  });

  it("shows Edit to an admin on another person's report", () => {
    expect(markup(row({ mine: false, can_edit: true }))).toContain("Edit");
  });

  it("shows no Edit to a member who is not the author", () => {
    const html = markup(row({ mine: false, can_edit: false }));
    expect(html).not.toContain("Edit");
    expect(html).toContain("Home");
  });

  it("shows no Edit when the server sent no flag", () => {
    expect(markup(row({}))).not.toContain("Edit");
  });
});
