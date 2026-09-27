/**
 * The subtask tree (D-PM-38, Subtasks S2). Moved here from `table.test.ts`
 * with the helper, and extended: any depth, orphans, cycles, and counts that
 * do not depend on what is collapsed.
 */

import { describe, expect, it } from "vitest";

import { treeRows } from "./taskTree";

const t = (id: string, parent?: string | null) => ({
  id,
  parent_task_id: parent ?? null,
});

const NONE: ReadonlySet<string> = new Set();

const shape = (rows: ReturnType<typeof treeRows>) =>
  rows.map((r) => [r.task.id, r.depth]);

describe("treeRows — subtasks indent under a parent in the set", () => {
  it("nests children directly under their parent, depth-first", () => {
    const rows = treeRows([t("a"), t("b"), t("a1", "a"), t("a1x", "a1")], NONE);
    expect(shape(rows)).toEqual([
      ["a", 0],
      ["a1", 1],
      ["a1x", 2],
      ["b", 0],
    ]);
  });

  it("goes to any depth — five levels, in order", () => {
    const rows = treeRows(
      [t("l4", "l3"), t("l2", "l1"), t("l0"), t("l3", "l2"), t("l1", "l0")],
      NONE,
    );
    expect(shape(rows)).toEqual([
      ["l0", 0],
      ["l1", 1],
      ["l2", 2],
      ["l3", 3],
      ["l4", 4],
    ]);
  });

  it("does not overflow the stack on a very deep chain", () => {
    const chain = Array.from({ length: 20_000 }, (_, i) =>
      t(`n${i}`, i ? `n${i - 1}` : null),
    );
    const rows = treeRows(chain, NONE);
    expect(rows).toHaveLength(20_000);
    expect(rows[19_999].depth).toBe(19_999);
  });

  it("counts direct children so the caret knows when to draw", () => {
    const rows = treeRows([t("a"), t("a1", "a"), t("a2", "a")], NONE);
    expect(rows[0].childCount).toBe(2);
    expect(rows[1].childCount).toBe(0);
  });

  it("keeps the incoming order within each level — the caller sorted it", () => {
    const rows = treeRows([t("b"), t("a"), t("b2", "b"), t("b1", "b")], NONE);
    expect(rows.map((r) => r.task.id)).toEqual(["b", "b2", "b1", "a"]);
  });
});

describe("orphans — decision 3: the subtask is shown alone", () => {
  it("surfaces a subtask whose parent is not in the set, flat, and says so", () => {
    const rows = treeRows([t("orphan", "elsewhere"), t("a")], NONE);
    expect(shape(rows)).toEqual([
      ["orphan", 0],
      ["a", 0],
    ]);
    expect(rows.map((r) => r.orphan)).toEqual([true, false]);
  });

  it("an orphan's own children still nest under it", () => {
    const rows = treeRows([t("o", "gone"), t("o1", "o")], NONE);
    expect(shape(rows)).toEqual([
      ["o", 0],
      ["o1", 1],
    ]);
    expect(rows.map((r) => r.orphan)).toEqual([true, false]);
  });

  it("a top-level task is not an orphan, and neither is a nested child", () => {
    const rows = treeRows([t("a"), t("a1", "a")], NONE);
    expect(rows.every((r) => !r.orphan)).toBe(true);
  });
});

describe("collapse hides rows, never changes the counts", () => {
  const tree = [t("a"), t("a1", "a"), t("a1x", "a1"), t("a2", "a"), t("b")];

  it("hides a collapsed parent's whole subtree, grandchildren included", () => {
    const rows = treeRows(tree, new Set(["a"]));
    expect(rows.map((r) => r.task.id)).toEqual(["a", "b"]);
  });

  it("collapsing a mid-level node keeps its siblings", () => {
    const rows = treeRows(tree, new Set(["a1"]));
    expect(rows.map((r) => r.task.id)).toEqual(["a", "a1", "a2", "b"]);
  });

  it("childCount and descendantCount are the same collapsed or open", () => {
    const counts = (collapsed: ReadonlySet<string>) =>
      Object.fromEntries(
        treeRows(tree, collapsed).map((r) => [
          r.task.id,
          [r.childCount, r.descendantCount],
        ]),
      );
    const open = counts(NONE);
    expect(open.a).toEqual([2, 3]);
    expect(open.a1).toEqual([1, 1]);
    expect(counts(new Set(["a"])).a).toEqual(open.a);
    expect(counts(new Set(["a1"])).a1).toEqual(open.a1);
  });
});

describe("cycles — impossible server-side, survivable here", () => {
  it("loses no rows to a parent cycle, and does not hang", () => {
    const rows = treeRows([t("x", "y"), t("y", "x")], NONE);
    expect(rows.map((r) => r.task.id).sort()).toEqual(["x", "y"]);
  });

  it("a three-node loop keeps every row, and the counts are finite", () => {
    const rows = treeRows([t("p", "r"), t("q", "p"), t("r", "q")], NONE);
    expect(rows).toHaveLength(3);
    for (const row of rows) expect(row.descendantCount).toBeLessThanOrEqual(2);
  });

  it("treats a self-parented row as a root rather than recursing", () => {
    const rows = treeRows([t("a", "a")], NONE);
    expect(rows.map((r) => [r.depth, r.descendantCount])).toEqual([[0, 0]]);
  });
});
