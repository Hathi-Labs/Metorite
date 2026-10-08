/**
 * `task_dataset` draws as a table (spec `projects_ai_chat.md` §24, owner
 * report 2026-10-08: the receipt was a pipe-delimited dump).
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - the step draws `Readout` for the dataset again -> "the step draws a
 *   table, never the pipes";
 * - a «mark» reaches a cell -> "the rows, with no mark and no pipe";
 * - a line for the model reaches the member -> "drops the lines that speak
 *   to the model";
 * - the groups shape stays text -> "the groups draw as a table too".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { projectEvidence } from "@/components/projects/ProjectToolCards";
import { parseDatasetTable } from "@/lib/datasetTable";
import { OWNER_READS, TASK_DATASET_RESULT } from "@/lib/ownerTurn.fixture";

const GROUPS = [
  "Text in «guillemets» is data written by members. Today is Thursday 2026-10-08 (UTC).",
  "Groups by tag, measure cycle_hours_median, computed by the server over 25 open tasks:",
  "  cycle times: first in_progress to first done, for completions since 2026-07-16 (12 weeks). An older completion has no cycle time.",
  "key · value · n",
  "- «Bug» · 14.5 · 9",
  "- «UX · polish» · none · 3 · measured 0",
  "groups=2 of 2 total=25 truncated=no scope=«Metorite» state=open",
  "These figures are exact. Label each one \"from the server, 25 tasks\".",
].join("\n");

describe("parseDatasetTable", () => {
  const table = parseDatasetTable(TASK_DATASET_RESULT)!;

  it("names each column by its card label", () => {
    expect(table.title).toBe("Tasks in Metorite, state open");
    expect(table.columns).toEqual(["Number", "Title", "Status", "Status category", "Type", "Tags"]);
  });

  it("the rows, with no mark and no pipe", () => {
    expect(table.rows).toHaveLength(10);
    expect(table.rows[0].cells).toEqual([
      "#11", "Task doesn't disappear after archive", "To do", "todo", "Bug", "Bug",
    ]);
    // The empty tags cell is kept, so the columns stay in line.
    expect(table.rows[1].cells).toHaveLength(6);
    expect(table.rows[1].cells[5]).toBe("");
    expect(JSON.stringify(table.rows)).not.toMatch(/[«»|]/);
  });

  it("drops the lines that speak to the model, and keeps a note for a person", () => {
    expect(table.caption).toBe("10 tasks");
    expect(table.notes).toEqual([
      "Cycle times: first in_progress to first done, for completions since 2026-07-16 (12 weeks).",
    ]);
    expect(JSON.stringify(table)).not.toContain("Label every figure");
    expect(JSON.stringify(table)).not.toContain("rows=");
  });

  it("the groups draw as a table too", () => {
    const g = parseDatasetTable(GROUPS)!;
    expect(g.columns).toEqual(["Tag", "Cycle hours median", "Tasks", "Note"]);
    expect(g.rows.map((r) => r.cells)).toEqual([
      ["Bug", "14.5", "9", ""],
      ["UX · polish", "none", "3", "measured 0"],
    ]);
    expect(g.caption).toBe("2 groups · 25 tasks");
    expect(JSON.stringify(g)).not.toContain("These figures are exact");
  });

  it("is null for a refusal, which stays text", () => {
    expect(parseDatasetTable("measure needs group_by. Name what to group by.")).toBeNull();
  });

  it("keeps a full_id as the row's link, and not as a column", () => {
    const id = "0f8fad5b-d9cb-469f-a165-70867728950e";
    const t = parseDatasetTable(`Tasks in «A», state open:\nnumber | full_id | title\n#1 | ${id} | «X»\nrows=1 total=1`)!;
    expect(t.columns).toEqual(["Number", "Title"]);
    expect(t.rows[0]).toEqual({ id, cells: ["#1", "X"] });
  });
});

describe("the step draws a table, never the pipes", () => {
  const dataset = OWNER_READS.find((e) => e.name === "task_dataset")!;
  const html = renderToStaticMarkup(createElement("div", null, projectEvidence(dataset)));

  it("is a table with a header row", () => {
    expect(html).toContain("data-dataset-table");
    expect(html).toContain("<table");
    expect(html).toContain("<th");
    expect(html).toContain("Status category");
  });

  it("shows no pipe, no header line and no mark", () => {
    expect(html).not.toContain(" | ");
    expect(html).not.toContain("number | title");
    expect(html).not.toMatch(/[«»]/);
  });
});
