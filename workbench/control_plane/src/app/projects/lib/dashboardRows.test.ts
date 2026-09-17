/**
 * The owner's question, pinned: a project with subprojects AND its own tasks.
 *
 * ⚠️ The server half of this is `tests/unit/test_projects_node_summary.py`.
 * Both halves matter and neither substitutes — the server can return a
 * correct `own` block that this file then declines to draw, which is the
 * exact shape of the three UI-only defects wave 4 shipped.
 */
import { describe, expect, it } from "vitest";

import type { NodeSummary, SummaryChild } from "./api";
import { dashboardRows, emptyCopy, rowsReconcile } from "./dashboardRows";

function child(id: string, tasks: number, overdue = 0): SummaryChild {
  return {
    id,
    name: id,
    kind: "project",
    archived: false,
    tasks,
    overdue,
    by_category: { todo: tasks },
  };
}

function summary(over: Partial<NodeSummary> = {}): NodeSummary {
  return {
    id: "P",
    name: "Parent",
    level: "project",
    tasks: 0,
    overdue: 0,
    by_category: {},
    projects: 0,
    children: [],
    ...over,
  };
}

describe("the node's own work", () => {
  it("draws first, ahead of every child", () => {
    const rows = dashboardRows(
      summary({
        tasks: 25,
        children: [child("A", 8), child("B", 5)],
        own: { tasks: 12, overdue: 2, by_category: { todo: 12 } },
      })
    );
    expect(rows.map((r) => r.kind)).toEqual(["own", "child", "child"]);
    expect(rows[0]).toMatchObject({ kind: "own", tasks: 12, overdue: 2 });
  });

  it("makes the rows add up to the strip", () => {
    const s = summary({
      tasks: 25,
      children: [child("A", 8), child("B", 5)],
      own: { tasks: 12, overdue: 0, by_category: {} },
    });
    const drawn = dashboardRows(s).reduce(
      (sum, r) => sum + (r.kind === "own" ? r.tasks : r.child.tasks),
      0
    );
    expect(drawn).toBe(s.tasks);
    expect(rowsReconcile(s)).toBe(true);
  });

  it("⚠️ is the defect this file exists for: without it the page loses 12", () => {
    // The shape the server used to return. Kept as a test rather than a
    // comment, because it is what a reader of this file needs to see to
    // understand why `own` is worth a block of its own.
    const before = summary({
      tasks: 25,
      children: [child("A", 8), child("B", 5)],
    });
    expect(rowsReconcile(before)).toBe(false);
    const drawn = dashboardRows(before).reduce(
      (sum, r) => sum + (r.kind === "own" ? r.tasks : r.child.tasks),
      0
    );
    expect(before.tasks - drawn).toBe(12);
  });

  it("is not drawn on a leaf — the Progress card already says it", () => {
    const rows = dashboardRows(
      summary({
        level: "subproject",
        tasks: 4,
        children: [],
        own: { tasks: 4, overdue: 0, by_category: { todo: 4 } },
      })
    );
    expect(rows).toEqual([]);
  });

  it("is not drawn when the node owns nothing", () => {
    // A row reading 0 beside real rows reads as a figure that failed to
    // load, which is the confusion `Stat`'s dash exists to prevent.
    const rows = dashboardRows(
      summary({
        tasks: 8,
        children: [child("A", 8)],
        own: { tasks: 0, overdue: 0, by_category: {} },
      })
    );
    expect(rows.map((r) => r.kind)).toEqual(["child"]);
  });

  it("is not invented when the server did not send it", () => {
    // ⚠️ Absent is not zero. A deployment older than the `own` block must
    // draw what it drew before, not a row claiming the node owns nothing.
    const rows = dashboardRows(
      summary({ tasks: 8, children: [child("A", 8)] })
    );
    expect(rows.map((r) => r.kind)).toEqual(["child"]);
  });

  it("carries the node's own overdue count, not the subtree's", () => {
    const rows = dashboardRows(
      summary({
        tasks: 10,
        overdue: 4,
        children: [child("A", 7, 1)],
        own: { tasks: 3, overdue: 3, by_category: { todo: 3 } },
      })
    );
    expect(rows[0]).toMatchObject({ kind: "own", overdue: 3 });
  });
});

describe("reading a response that is missing fields", () => {
  // `api.call` casts rather than validates. Every field below is typed as
  // present and arrived absent at least once in production.
  it("survives children being absent", () => {
    const s = { ...summary({ tasks: 0 }), children: undefined } as never;
    expect(dashboardRows(s)).toEqual([]);
  });

  it("survives own being a partial object", () => {
    const s = summary({
      tasks: 5,
      children: [child("A", 2)],
      own: { tasks: 3 } as never,
    });
    expect(dashboardRows(s)[0]).toMatchObject({
      kind: "own",
      tasks: 3,
      overdue: 0,
      by_category: {},
    });
  });

  it("reconciles when the children already account for everything", () => {
    // No `own`, and nothing is missing.
    expect(rowsReconcile(summary({ tasks: 8, children: [child("A", 8)] }))).toBe(
      true
    );
  });

  it("⚠️ still reports a mismatch against an older server", () => {
    // A deployment with no `own` block genuinely does leave the parent's own
    // tasks out of every row. Staying quiet would suppress an honest number
    // to keep a deploy window tidy, which is how the hole stayed invisible.
    expect(
      rowsReconcile(summary({ tasks: 20, children: [child("A", 8)] }))
    ).toBe(false);
  });
});

describe("the empty-column copy", () => {
  it("⚠️ never says empty above a strip that counts work", () => {
    // Found in review 2026-09-17. `assert_node_grammar` refuses a task on a
    // FOLDER and accepts one on a space, so this state is reachable through
    // the API. The old copy said "This space is empty" over a strip of 5.
    const copy = emptyCopy(
      summary({ level: "space", tasks: 5, children: [], own: { tasks: 5, overdue: 0, by_category: {} } })
    );
    expect(copy).not.toMatch(/empty/i);
    expect(copy).toContain("5 tasks sit directly in this space");
  });

  it("says it in the singular for one task", () => {
    expect(
      emptyCopy(summary({ level: "folder", tasks: 1, children: [] }))
    ).toContain("1 task sits directly in this folder");
  });

  it("keeps the ordinary invitation when there is genuinely no work", () => {
    expect(emptyCopy(summary({ level: "space", tasks: 0 }))).toContain(
      "This space is empty"
    );
  });

  it("tells a project its task views hold the work", () => {
    expect(emptyCopy(summary({ level: "project", tasks: 9 }))).toContain(
      "No subprojects"
    );
  });

  it("does not render a bare box for a level it does not know", () => {
    const copy = emptyCopy(summary({ level: "galaxy" as never, tasks: 0 }));
    expect(copy.trim().length).toBeGreaterThan(0);
  });
});
