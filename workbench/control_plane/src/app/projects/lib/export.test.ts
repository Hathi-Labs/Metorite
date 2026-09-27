/**
 * WS-27ae — the export's client half.
 *
 * The one claim that matters: the export asks for the SAME thing the board is
 * showing. It is asserted by comparing against `toQuery`/`sortQuery` directly
 * rather than by restating the parameter names, because a test that restates
 * them passes just as happily against a second serialisation.
 */

import { describe, expect, it } from "vitest";

import { canExport, exportPath, exportQuery } from "./export";
import { EMPTY_FILTERS, type Filters, toQuery } from "./grouping";
import { type TableSort, sortQuery } from "./table";

const FILTERED: Filters = {
  ...EMPTY_FILTERS,
  q: "release",
  statusCategory: "in_progress",
  assignee: "priya@fracktal.in",
  overdue: true,
  tags: ["bug", "ops"],
};

const SORT: TableSort = { key: "due_at", dir: "asc" };

describe("exportQuery", () => {
  it("sends exactly the filters the list fetch sends", () => {
    // ⚠️ Compared against `toQuery` itself. Restating the parameter names here
    // would pass against a second serialisation, which is the drift this
    // module exists to avoid.
    const query = exportQuery({
      projectId: "p1",
      filters: FILTERED,
      shownFields: ["status"],
      sort: null,
    });
    for (const [key, value] of Object.entries(toQuery(FILTERED))) {
      expect(query[key]).toBe(value);
    }
  });

  it("sends the table's sort so the file's order is the screen's", () => {
    const query = exportQuery({
      projectId: "p1",
      filters: EMPTY_FILTERS,
      shownFields: [],
      sort: SORT,
    });
    expect(query).toMatchObject(sortQuery(SORT));
  });

  it("sends no sort when the table has none", () => {
    const query = exportQuery({
      projectId: "p1",
      filters: EMPTY_FILTERS,
      shownFields: [],
      sort: null,
    });
    expect(query.sort).toBeUndefined();
    expect(query.direction).toBeUndefined();
  });

  it("sends the shown fields as the CSV the gateway splits", () => {
    const query = exportQuery({
      projectId: "p1",
      filters: EMPTY_FILTERS,
      shownFields: ["status", "due_at", "custom.risk"],
      sort: null,
    });
    expect(query.shown_fields).toBe("status,due_at,custom.risk");
  });

  it("widens a project to its subtree, as the board's own fetch does", () => {
    // Without it the file would be missing most of the rows on screen: the
    // board reads `include_subtree: true`.
    const query = exportQuery({
      projectId: "p1",
      filters: EMPTY_FILTERS,
      shownFields: [],
      sort: null,
    });
    expect(query).toMatchObject({ project_id: "p1", include_subtree: "true" });
  });

  it("omits the project entirely when the board is showing everything", () => {
    const query = exportQuery({
      projectId: null,
      filters: EMPTY_FILTERS,
      shownFields: [],
      sort: null,
    });
    expect(query.project_id).toBeUndefined();
    expect(query.include_subtree).toBeUndefined();
  });

  it("keeps an explicitly empty column set rather than dropping it", () => {
    // "absent" and "explicitly empty" are different facts on both sides of the
    // wire — the same distinction `sanitizeShownFields` keeps.
    expect(
      exportQuery({
        projectId: null,
        filters: EMPTY_FILTERS,
        shownFields: [],
        sort: null,
      }).shown_fields
    ).toBe("");
  });
});

describe("exportPath", () => {
  it("goes through the BFF proxy, never at the gateway", () => {
    const path = exportPath({
      projectId: "p1",
      filters: EMPTY_FILTERS,
      shownFields: ["status"],
      sort: null,
    });
    expect(path.startsWith("/api/projects/export/tasks.csv?")).toBe(true);
  });

  it("escapes a filter term rather than pasting it into the query", () => {
    const path = exportPath({
      projectId: null,
      filters: { ...EMPTY_FILTERS, q: "a&b c" },
      shownFields: [],
      sort: null,
    });
    expect(path).toContain("q=a%26b+c");
  });
});

// `filenameFromDisposition` moved to `@/lib/export` with the CRM's export
// (WS-26i-export); its cases live in `src/lib/export.test.ts` beside it.

describe("canExport", () => {
  it("does not require a project — exporting everything you can see is real", () => {
    expect(
      canExport({
        projectId: null,
        filters: EMPTY_FILTERS,
        shownFields: [],
        sort: null,
      })
    ).toBe(true);
  });
});

// ── D-PM-38 (S3): the file matches the screen ───────────────────────────────

describe("exportQuery and the Subtasks mode", () => {
  const request = {
    projectId: "p1",
    filters: EMPTY_FILTERS,
    shownFields: ["status"],
    sort: null,
  };

  it("a Hidden board exports its top-level tasks only", () => {
    expect(exportQuery({ ...request, subtasks: "hidden" }).top_level).toBe("true");
    expect(exportPath({ ...request, subtasks: "hidden" })).toContain("top_level=true");
  });

  it("every other mode sends no flag", () => {
    for (const subtasks of ["nested", "separate", null, undefined] as const) {
      expect(exportQuery({ ...request, subtasks })).not.toHaveProperty("top_level");
    }
  });
});
