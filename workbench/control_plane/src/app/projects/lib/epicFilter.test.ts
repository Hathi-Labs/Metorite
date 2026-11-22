/**
 * SP-2a — the Epic filter.
 *
 * Mutations caught, by test:
 * - only direct children stay: "keeps a grandchild";
 * - the epic itself drops: "keeps the epic itself";
 * - an empty id filters: "returns every row for no epic";
 * - a parent loop hangs: "ends on a parent loop".
 */
import { describe, expect, it } from "vitest";

import { epicOptions, filterByEpic } from "./epicFilter";

const rows = [
  { id: "e1", parent_task_id: null, type_id: "ep", title: "Launch", task_number: 7 },
  { id: "a", parent_task_id: "e1", type_id: null, title: "A", task_number: 8 },
  { id: "b", parent_task_id: "a", type_id: null, title: "B", task_number: 9 },
  { id: "e2", parent_task_id: null, type_id: "ep", title: "Other", task_number: null },
  { id: "c", parent_task_id: "e2", type_id: null, title: "C", task_number: 10 },
  { id: "d", parent_task_id: null, type_id: null, title: "D", task_number: 11 },
];

describe("the Epic filter", () => {
  it("keeps the epic itself", () => {
    expect(filterByEpic(rows, "e1").map((t) => t.id)).toContain("e1");
  });
  it("keeps a grandchild", () => {
    expect(filterByEpic(rows, "e1").map((t) => t.id).sort()).toEqual(["a", "b", "e1"]);
  });
  it("returns every row for no epic", () => {
    expect(filterByEpic(rows, "")).toHaveLength(rows.length);
  });
  it("ends on a parent loop", () => {
    const loop = [
      { id: "x", parent_task_id: "y" },
      { id: "y", parent_task_id: "x" },
    ];
    expect(filterByEpic(loop, "x").map((t) => t.id).sort()).toEqual(["x", "y"]);
  });
  it("lists the epics with their number", () => {
    const types = [{ id: "ep", name: "Epic", is_epic: true }];
    expect(epicOptions(rows, types)).toEqual([
      { value: "e1", label: "#7 Launch" },
      { value: "e2", label: "Other" },
    ]);
  });
});
