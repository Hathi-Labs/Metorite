/**
 * #834 — a parent project's board never loses a task that sits in a
 * subproject's own lane.
 *
 * R7 fences named here:
 *   * `parent-board-own-lane-shows`: by status, the task lands in the first
 *     board lane of its stage. By stage, it lands in its stage.
 *   * `parent-board-own-lane-reorder-no-write`: a drag inside that column is
 *     a reorder, never a status write over the task's own lane.
 *   * `gateway-stamps-status-category`: the list read stamps the stage on each
 *     row (`attach_status_category`).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { StatusRow, TaskRow } from "./api";
import { buildCellDropPatch } from "./board";
import { groupTasks } from "./grouping";

const lane = (id: string, category: string, position: number): StatusRow =>
  ({ id, project_id: "parent", name: id, category, position }) as StatusRow;

// The parent's lanes. The subproject keeps "doing-sub" (in_progress) and
// "shipped-sub" (done) of its own.
const statuses = [lane("todo", "todo", 10), lane("wip", "in_progress", 20), lane("review", "in_progress", 30), lane("done", "done", 40)];

const task = (id: string, status_id: string, status_category?: string | null): TaskRow =>
  ({ id, title: id, status_id, status_category, project_id: "sub" }) as unknown as TaskRow;

const rows = [
  task("a", "wip", "in_progress"),
  task("b", "doing-sub", "in_progress"),
  task("c", "shipped-sub", "done"),
  task("d", "mystery", null),
];

describe("parent-board-own-lane-shows", () => {
  it("by status, an own-lane task lands in the first lane of its stage", () => {
    const groups = groupTasks(rows, "status", { statuses });
    const ids = Object.fromEntries(groups.map((g) => [g.key, g.tasks.map((t) => t.id)]));
    expect(ids).toEqual({ todo: ["d"], wip: ["a", "b"], review: [], done: ["c"] });
    // Every task is on the board.
    expect(groups.flatMap((g) => g.tasks).length).toBe(rows.length);
  });

  it("by stage, an own-lane task lands in its stage", () => {
    const groups = groupTasks(rows, "category", { statuses });
    const ids = Object.fromEntries(groups.map((g) => [g.key, g.tasks.map((t) => t.id)]));
    expect(ids.in_progress).toEqual(["a", "b"]);
    expect(ids.done).toEqual(["c"]);
  });
});

describe("parent-board-own-lane-reorder-no-write", () => {
  it("a drop in the column it already draws in writes no status", () => {
    expect(buildCellDropPatch(rows[1], "status", "wip", null, null, statuses)).toBeNull();
    expect(buildCellDropPatch(rows[2], "category", "done", null, null, statuses)).toBeNull();
  });

  it("a drop in another column still moves it", () => {
    expect(buildCellDropPatch(rows[1], "status", "done", null, null, statuses)).toEqual({ status_id: "done" });
  });
});

describe("gateway-stamps-status-category", () => {
  it("the task list read stamps the stage on every row", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../../../../../../apps/services/gateway/gateway/routes/projects/tasks.py", import.meta.url)),
      "utf-8",
    );
    expect(src).toMatch(/await attach_status_category\(db, vis, page_rows\)\s*\n\s*if with_overlay:/);
  });
});
