/**
 * The `dataGrid` template's column rules and its stacked layout (follow-up
 * of #716 and #735, spec `projects_ai_chat.md` §24.8). The owner's dataset
 * table in the Projects rail drew the raw category beside the status, wrapped
 * the title one word per line, and cut the last column with no cue.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `gridColumns` keeps the category column beside a status -> "hides a
 *   category column when a status column says it";
 * - the primary column falls back to index 1 again -> "gives the title the
 *   priority, by its label";
 * - `gridLayout` ignores the width, or the root font size -> "stacks under
 *   the table's least width" and "follows the density";
 * - the stacked rows lose the labels of their facts -> "a stacked row is the
 *   title, then chips and labelled facts";
 * - a category cell draws its raw key -> "the status label mapping".
 *
 * Review round 1, each run red before its fix:
 *
 * - a model's own "Category" column hides beside "Status" -> "keeps a
 *   model's Category column";
 * - the primary cell of a groups-by-stage table draws as text -> "a stage
 *   in the title column reads as its label";
 * - a cell past the last label drops -> "labels a cell past the last
 *   column".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { DataGrid } from "@/components/genUITemplates";
import { ValueElement } from "@/components/CardFieldValue";
import { cellText, gridColumns, gridLabels, gridLayout, isEmptyCell, minTableRem, splitMany } from "@/lib/dataGridLayout";
import { statusAccent } from "@/lib/statusAccent";
import { categoryLabel } from "@/lib/statusCategory";

const DATASET = ["Number", "Title", "Status", "Status category", "Type", "Tags"];
const KINDS = [undefined, undefined, "status", "category", "words", "tag"];

describe("the column rules", () => {
  it("hides a category column when a status column says it", () => {
    const g = gridColumns(DATASET, KINDS);
    expect(g.shown.map((c) => c.label)).toEqual(["Number", "Title", "Status", "Type", "Tags"]);
    // The hidden cell still colours the status chip.
    expect(g.categoryAt).toBe(3);
  });

  it("keeps a category column when no status column is there", () => {
    const g = gridColumns(["Title", "Status category"], [undefined, "category"]);
    expect(g.shown.map((c) => c.kind)).toEqual(["text", "category"]);
    expect(g.categoryAt).toBe(-1);
  });

  it("gives the title the priority, by its label", () => {
    expect(gridColumns(DATASET, KINDS).primaryAt).toBe(1);
    // An agent's own grid: no number column, the title is first.
    expect(gridColumns(["Task", "Owner"]).primaryAt).toBe(0);
    // The number is the lead, never the title.
    const g = gridColumns(["#", "Title", "Status", "Assignees", "Due", "Priority"]);
    expect(g.shown.map((c) => c.role)).toEqual(["lead", "primary", "secondary", "secondary", "secondary", "secondary"]);
  });

  it("takes a kind from the card label when the data names none", () => {
    // `render_tasks` sends labels only.
    const g = gridColumns(["#", "Title", "Status", "Assignees", "Due", "Priority"]);
    expect(g.shown.map((c) => c.kind)).toEqual(["text", "text", "status", "person", "date", "text"]);
  });
});

describe("review round 1", () => {
  it("keeps a model's Category column", () => {
    // `emit_generative_ui` sends labels only, and "Travel" is not a stage.
    const g = gridColumns(["Item", "Category", "Status"]);
    expect(g.shown.map((c) => c.label)).toEqual(["Item", "Category", "Status"]);
    expect(g.shown[1].kind).toBe("text");
    expect(g.categoryAt).toBe(-1);
  });

  it("a stage in the title column reads as its label", () => {
    const out = renderToStaticMarkup(
      createElement(DataGrid, {
        data: { columns: ["Status category", "Count", "Tasks"], kinds: ["category"], rows: [{ cells: ["in_progress", "4", "4"] }] },
        layout: "table",
      }),
    );
    expect(out).toContain(">In progress<");
    expect(out.replace(/<[^>]*>/g, "|")).not.toContain("|in_progress|");
  });

  it("labels a cell past the last column", () => {
    expect(gridLabels(["A"], [{ cells: ["x", "y", "z"] }])).toEqual(["A", "Column 2", "Column 3"]);
    const out = renderToStaticMarkup(
      createElement(DataGrid, { data: { columns: ["Title"], rows: [{ cells: ["X", "extra"] }] }, layout: "table" }),
    );
    expect(out).toContain(">Column 2<");
    expect(out).toContain(">extra<");
  });
});

describe("the stacked layout", () => {
  const shown = gridColumns(DATASET, KINDS).shown;

  it("stacks under the table's least width", () => {
    // Number 3 + Title 10 + Status 6.5 + Type 5 + Tags 5.5 = 30rem.
    expect(minTableRem(shown)).toBe(30);
    expect(gridLayout(30 * 16 - 1, shown)).toBe("stacked");
    expect(gridLayout(30 * 16, shown)).toBe("table");
    // The Projects rail is about 26rem wide, and a phone 390px.
    expect(gridLayout(26 * 16, shown)).toBe("stacked");
    expect(gridLayout(390, shown)).toBe("stacked");
    expect(gridLayout(720, shown)).toBe("table");
  });

  it("follows the density", () => {
    // Compact density makes the root font smaller, so the same box holds the table.
    expect(gridLayout(450, shown, 16)).toBe("stacked");
    expect(gridLayout(450, shown, 14)).toBe("table");
  });

  it("draws the table before the box is measured, and for two columns", () => {
    expect(gridLayout(0, shown)).toBe("table");
    expect(gridLayout(100, gridColumns(["Tag", "Tasks"]).shown)).toBe("table");
  });
});

describe("a cell of several values", () => {
  it("splits at a comma outside brackets", () => {
    expect(splitMany("Priya (priya@x.io), Sam")).toEqual(["Priya (priya@x.io)", "Sam"]);
    expect(splitMany(["UX, polish", "Bug"])).toEqual(["UX, polish", "Bug"]);
    expect(cellText(["a", "b"])).toBe("a, b");
  });

  it("knows an empty cell", () => {
    for (const c of ["", "—", "unassigned", [], null]) expect(isEmptyCell(c)).toBe(true);
    expect(isEmptyCell("Bug")).toBe(false);
  });
});

describe("the dataGrid template", () => {
  const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
  const data = {
    title: "Tasks",
    columns: DATASET,
    kinds: KINDS,
    rows: [
      { id: ID, cells: ["#11", "Task doesn't disappear after archive", "In review", "in_progress", "Bug", ["Bug", "UX"]] },
      { cells: ["#12", "Board column colours drift", "To do", "todo", "Feature", []] },
    ],
  };
  const html = (layout: "table" | "stacked") =>
    renderToStaticMarkup(createElement(DataGrid, { data, layout }));

  it("a stacked row is the title, then chips and labelled facts", () => {
    const out = html("stacked");
    expect(out).toContain('data-grid-layout="stacked"');
    expect(out).not.toContain("<table");
    expect(out.match(/data-grid-row=""/g) ?? []).toHaveLength(2);
    // A text fact carries its label. A chip speaks for itself.
    expect(out).toMatch(/data-grid-fact=""[^>]*><span[^>]*>Type <\/span>Bug/);
    expect(out).toContain(statusAccent({ category: "in_progress", name: "In review" }).dot);
    // The title links to the task.
    expect(out).toContain(`href="/projects?task=${ID}"`);
  });

  it("the table gives the title a least width and wraps it", () => {
    const out = html("table");
    expect(out).toContain("<table");
    expect(out).toMatch(/min-width:10rem;overflow-wrap:anywhere/);
    expect(out).not.toContain(">Status category<");
  });

  it("draws no raw category in either layout", () => {
    for (const out of [html("table"), html("stacked")]) {
      const visible = out.replace(/<[^>]*>/g, "|");
      expect(visible).not.toMatch(/\|(?:todo|in_progress)\|/);
    }
  });
});

describe("the status label mapping", () => {
  it("maps each category to its readable label", () => {
    expect(categoryLabel("in_progress")).toBe("In progress");
    expect(categoryLabel("todo")).toBe("To do");
    expect(categoryLabel("backlog")).toBe("Backlog");
  });

  it("a category value draws as its label, fenced or not", () => {
    for (const named of [true, false]) {
      const out = renderToStaticMarkup(
        createElement(ValueElement, { item: { text: "in_progress", named }, kind: "category" }),
      );
      expect(out).toContain(">In progress<");
      expect(out).not.toContain("in_progress");
      expect(out).toContain(statusAccent({ category: "in_progress" }).dot);
    }
  });

  it("a category column with no status draws the label in the grid", () => {
    const out = renderToStaticMarkup(
      createElement(DataGrid, {
        data: { columns: ["Title", "Status category"], kinds: [undefined, "category"], rows: [{ cells: ["X", "todo"] }] },
        layout: "table",
      }),
    );
    expect(out).toContain(">To do<");
  });
});
