/**
 * #918 — a parent board's columns show the true count, and each column pages
 * its own rows.
 *
 * R7 fences named here:
 *   * `column-true-count`: a column counts the server's lane size across the
 *     whole filtered set, a subproject's own lane counts in the column it
 *     draws in, and a stage column sums its lanes.
 *   * `column-own-read`: one filter names a column's own read, or none when
 *     the column needs two status ids, and the next page follows the rows held.
 *   * `board-page-after-column-read`: the board's own next page leaves out
 *     the rows only a column read brought, so it skips nothing.
 *   * `board-shows-the-count`: the board asks for the counts, and TaskBoard
 *     draws them with a "Load N more" foot.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { EMPTY_FILTERS } from "./grouping";
import {
  TASK_PAGE_SIZE,
  boardReadParams,
  columnMoreCount,
  columnReadFilter,
  columnSizes,
  nextBoardPage,
  nextTaskPage,
  type StatusCounts,
} from "./paging";

const lanes = [
  { id: "todo", category: "todo", position: 10 },
  { id: "wip", category: "in_progress", position: 20 },
  { id: "review", category: "in_progress", position: 30 },
  { id: "done", category: "done", position: 40 },
];

// The subproject keeps "shipped-sub" (done) of its own.
const sc: StatusCounts = {
  counts: { todo: 12, wip: 5, review: 3, done: 140, "shipped-sub": 60 },
  categories: { "shipped-sub": "done" },
};

describe("column-true-count", () => {
  it("by status, each column counts its lane, and an own lane counts where it draws", () => {
    const m = columnSizes("status", lanes, sc)!;
    expect(Object.fromEntries(m)).toEqual({ todo: 12, wip: 5, review: 3, done: 200 });
  });

  it("by stage, a column sums every lane of that stage", () => {
    const m = columnSizes("category", lanes, sc)!;
    expect(Object.fromEntries(m)).toEqual({ todo: 12, in_progress: 8, done: 200 });
  });

  it("no counts, or another grouping, keeps the rows held", () => {
    expect(columnSizes("status", lanes, null)).toBeNull();
    expect(columnSizes("assignee", lanes, sc)).toBeNull();
  });
});

describe("column-own-read", () => {
  it("a plain status column reads by its status id", () => {
    expect(columnReadFilter("status", "wip", lanes, sc)).toEqual({ status_id: "wip" });
  });

  it("a status column that also holds an own lane falls back to the board read", () => {
    expect(columnReadFilter("status", "done", lanes, sc)).toBeNull();
  });

  it("a stage column reads by its stage, which takes the own lanes too", () => {
    expect(columnReadFilter("category", "done", lanes, sc)).toEqual({ status_category: "done" });
  });

  it("the next page of a column follows the rows it holds, with no gap", () => {
    // 37 held: page 1 at 100 rows overlaps them, and the overlap is deduped.
    expect(nextTaskPage(37)).toBe(1);
    expect(nextTaskPage(100)).toBe(2);
    expect(columnMoreCount(37, 140)).toBe(TASK_PAGE_SIZE);
    expect(columnMoreCount(100, 140)).toBe(40);
    expect(columnMoreCount(140, 140)).toBe(0);
    expect(columnMoreCount(5, undefined)).toBe(0);
  });
});

describe("board-page-after-column-read", () => {
  it("counts only the board's own rows", () => {
    // 100 from the board's page 1, plus 100 only a column read brought.
    expect(nextBoardPage(200, 100)).toBe(2);
    // Counting the column rows too would have asked page 3 and skipped page 2.
    expect(nextTaskPage(200)).toBe(3);
  });
});

describe("board-shows-the-count", () => {
  const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");

  it("the board asks for the counts", () => {
    const bag = boardReadParams({ projectId: "p", filters: EMPTY_FILTERS, sort: null, viewId: null, subtasks: null });
    expect(bag.with_status_counts).toBe(true);
  });

  it("TaskBoard draws the server count and a Load N more foot", () => {
    const board = read("../components/TaskBoard.tsx");
    expect(board.match(/count: columnTotals\?\.get\(column\.key\) \?\? column\.tasks\.length/g)?.length).toBe(3);
    expect(board.match(/\{columnMore\(column\.key, column\.tasks\.length\)\}/g)?.length).toBe(2);
    const page = read("../page.tsx");
    expect(page).toMatch(/page: nextBoardPage\(tasks\.length, laneOnly\.size\)/);
    expect(page).toMatch(/columnTotals=\{columnTotals\}/);
  });
});
