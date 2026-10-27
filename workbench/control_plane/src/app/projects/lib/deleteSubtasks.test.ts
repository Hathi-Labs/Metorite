/**
 * D-OP-10 (OP-7) — the state of the delete dialog's subtask tree.
 *
 * The tree below is the spec's own example:
 *
 *   notes                      (a)
 *   beta list                  (b)
 *     waitlist                 (b1)
 *       import step            (b1a)   a grandchild
 *     invite email             (b2)
 *   demo call                  (c)
 */
import { describe, expect, it } from "vitest";

import type { SubtreeRow } from "./api";
import {
  afterDeleteTitle,
  buildRows,
  TOO_MANY_MESSAGE,
  confirmLabel,
  isAllTicked,
  tooManyPicked,
  countsOf,
  idsToDelete,
  keptHiddenNote,
  movesUp,
  setAll,
  summaryLine,
  toggleRow,
} from "./deleteSubtasks";

const row = (id: string, parent: string | null, title = id): SubtreeRow => ({
  id,
  parent_task_id: parent,
  title,
  status_name: "To do",
  status_category: "todo",
  archived: false,
});

// The route sends rows top down, by depth.
const SERVER: SubtreeRow[] = [
  row("a", "T"),
  row("b", "T"),
  row("c", "T"),
  row("b1", "b"),
  row("b2", "b"),
  row("b1a", "b1"),
];

describe("buildRows", () => {
  const rows = buildRows(SERVER);
  it("puts a child after its parent, with its depth", () => {
    expect(rows.map((r) => [r.id, r.depth])).toEqual([
      ["a", 1],
      ["b", 1],
      ["b1", 2],
      ["b1a", 3],
      ["b2", 2],
      ["c", 1],
    ]);
  });
  it("lists every descendant of a row, at any depth", () => {
    expect(rows.find((r) => r.id === "b")?.descendants).toEqual(["b1", "b1a", "b2"]);
  });
  it("draws a row under the task when its parent is hidden from the caller", () => {
    const shown = buildRows([row("a", "T"), row("g", "HIDDEN")]);
    expect(shown.map((r) => [r.id, r.depth, r.parentId])).toEqual([
      ["a", 1, null],
      ["g", 1, null],
    ]);
  });
  it("ends on a cycle instead of spinning", () => {
    expect(buildRows([row("x", "y"), row("y", "x")])).toEqual([]);
  });
});

describe("the boxes", () => {
  const rows = buildRows(SERVER);
  it("start clear, so the default is the old behaviour", () => {
    const counts = countsOf(new Set(), rows);
    expect(counts).toEqual({ deletes: 1, keeps: 6, hidden: 0 });
    expect(idsToDelete(new Set(), rows)).toEqual([]);
  });
  it("a tick on a parent ticks its whole subtree", () => {
    expect([...toggleRow(new Set(), rows, "b")].sort()).toEqual(["b", "b1", "b1a", "b2"]);
  });
  it("a clear on a child keeps the parent ticked", () => {
    const all = toggleRow(new Set(), rows, "b");
    const next = toggleRow(all, rows, "b2");
    expect(next.has("b")).toBe(true);
    expect(next.has("b2")).toBe(false);
    expect(next.has("b1a")).toBe(true);
  });
  it("a clear on a parent clears nothing below it", () => {
    const all = toggleRow(new Set(), rows, "b");
    const next = toggleRow(all, rows, "b");
    expect([...next].sort()).toEqual(["b1", "b1a", "b2"]);
  });
  it("Keep all and Delete all set every box", () => {
    expect(setAll(rows, true).size).toBe(6);
    expect(setAll(rows, false).size).toBe(0);
  });
});

describe("moves up", () => {
  const rows = buildRows(SERVER);
  it("a kept row under a ticked ancestor moves up", () => {
    const ticked = new Set(["b", "b1"]);
    expect(movesUp(ticked, rows, "b1a")).toBe(true);
    expect(movesUp(ticked, rows, "b2")).toBe(true);
  });
  it("a ticked row, and a kept row with no ticked ancestor, do not", () => {
    const ticked = new Set(["b", "b1"]);
    expect(movesUp(ticked, rows, "b")).toBe(false);
    expect(movesUp(ticked, rows, "a")).toBe(false);
    expect(movesUp(new Set(), rows, "b1a")).toBe(false);
  });
});

describe("the counts", () => {
  const rows = buildRows(SERVER);
  it("count the task itself, in the summary and on the button", () => {
    const ticked = new Set(["b", "b1"]);
    const counts = countsOf(ticked, rows);
    expect(counts).toEqual({ deletes: 3, keeps: 4, hidden: 0 });
    expect(summaryLine(counts)).toBe("Deletes 3 tasks · keeps 4");
    expect(confirmLabel(counts)).toBe("Delete 3 tasks");
    expect(idsToDelete(ticked, rows)).toEqual(["b", "b1"]);
  });
  it("agree when a subtask the member cannot see stays", () => {
    const all = new Set(rows.map((r) => r.id));
    expect(summaryLine(countsOf(all, rows, 1))).toBe(
      `Deletes ${rows.length + 1} tasks · keeps 0 · 1 you cannot see stays`,
    );
    expect(summaryLine(countsOf(all, rows, 2))).toContain("2 you cannot see stay");
  });
  it("send the all flag for a full tick and refuse a long list", () => {
    const big = buildRows(
      Array.from({ length: 260 }, (_, i) => ({
        id: `s${i}`, parent_task_id: null, title: `t${i}`, status_name: null,
        status_category: null, archived: false,
      })) as never,
    );
    expect(isAllTicked(setAll(big, true), big)).toBe(true);
    expect(tooManyPicked(setAll(big, true), big)).toBe(false);
    const most = new Set(big.slice(0, 250).map((r) => r.id));
    expect(tooManyPicked(most, big)).toBe(true);
    expect(TOO_MANY_MESSAGE).toBe("Pick up to 200 subtasks, or choose Delete all.");
  });
  it("say '1 task' for the task alone", () => {
    expect(confirmLabel(countsOf(new Set(), rows))).toBe("Delete 1 task");
  });
  it("match what the route reports after the delete", () => {
    const ticked = new Set(["b", "b1"]);
    const counts = countsOf(ticked, rows);
    // The route: subtasks_deleted = ids sent, subtasks_promoted = the kept
    // rows that moved. The toast restates them.
    expect(counts.deletes - 1).toBe(idsToDelete(ticked, rows).length);
    expect(afterDeleteTitle({ subtasks_promoted: 4, subtasks_deleted: 2 })).toBe(
      "Deleted. 2 subtasks deleted too. 4 subtasks moved up a level.",
    );
  });
});

describe("the words after a delete", () => {
  it("name the hidden subtasks that were kept, by count only", () => {
    expect(keptHiddenNote(0)).toBe("");
    expect(keptHiddenNote(1)).toBe("1 subtask you cannot see was kept.");
    expect(keptHiddenNote(3)).toBe("3 subtasks you cannot see were kept.");
    expect(afterDeleteTitle({ subtasks_promoted: 0, subtasks_kept_hidden: 3 })).toBe(
      "Deleted. 3 subtasks you cannot see were kept.",
    );
  });
  it("say 'Task deleted' when nothing else happened", () => {
    expect(afterDeleteTitle({ subtasks_promoted: 0 })).toBe("Task deleted");
  });
});
