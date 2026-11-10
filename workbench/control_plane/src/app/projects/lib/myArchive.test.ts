/**
 * ARC-1 fence (`one_projects_app.md` §9b.5): the Archive view lists the rows,
 * keeps a row of an archived project in the list with Restore off, and
 * restores one task or many through the two routes that exist.
 */
import { describe, expect, it } from "vitest";

import type { TaskRow } from "./api";
import {
  ARCHIVED_PROJECT_REASON,
  archiveQuery,
  archiveRows,
  pruneSelection,
  restoreBody,
  restoreFailure,
  restorableIds,
  restorePlan,
  restoreReceipt,
  restoreShortfall,
  restoredIds,
} from "./myArchive";

const task = (id: string, project_id: string, archived_at: string | null): TaskRow =>
  ({ id, title: `Task ${id}`, project_id, archived_at }) as unknown as TaskRow;

const PATHS: Record<string, string[]> = { p1: ["Space", "Alpha"], p2: ["Space", "Beta"] };
const pathOf = (id: string) => PATHS[id] ?? [];
const filed = (id: string) => id === "p2";

describe("the Archive read", () => {
  it("is the one list route over mine, filed tasks only", () => {
    const q = archiveQuery(2);
    expect(q.scope).toBe("mine");
    expect(q.archived_only).toBe("true");
    expect(q.page).toBe(2);
  });
});

describe("the Archive rows", () => {
  const rows = archiveRows(
    [
      task("a", "p1", "2026-11-01T10:00:00Z"),
      task("b", "p2", "2026-11-03T10:00:00Z"),
      task("c", "p1", "2026-11-02T10:00:00Z"),
      task("live", "p1", null),
    ],
    pathOf,
    filed,
  );

  it("lists every filed task, newest filed first, and no live task", () => {
    expect(rows.map((r) => r.task.id)).toEqual(["b", "c", "a"]);
  });

  it("names the project of each row", () => {
    expect(rows.find((r) => r.task.id === "a")?.path).toEqual(["Space", "Alpha"]);
  });

  it("keeps a row of an archived project, with Restore off", () => {
    const row = rows.find((r) => r.task.id === "b");
    expect(row?.projectArchived).toBe(true);
    expect(row?.canRestore).toBe(false);
    expect(ARCHIVED_PROJECT_REASON).toBe("Its project is archived. Restore the project first.");
  });

  it("selects only the rows that can come back", () => {
    expect(restorableIds(rows)).toEqual(["c", "a"]);
    expect([...pruneSelection(new Set(["a", "b", "gone"]), rows)]).toEqual(["a"]);
  });
});

describe("Restore", () => {
  it("uses the unarchive route for one task and the bulk route for many", () => {
    expect(restorePlan([])).toBeNull();
    expect(restorePlan(["a"])).toEqual({ kind: "one", id: "a" });
    expect(restorePlan(["a", "c"])).toEqual({ kind: "many", ids: ["a", "c"] });
    expect(restoreBody(["a", "c"])).toEqual({ task_ids: ["a", "c"], action: "unarchive" });
  });

  it("says what happened and what is left", () => {
    expect(restoreReceipt(1)).toBe("Task restored from the archive");
    expect(restoreReceipt(3)).toBe("3 tasks restored from the archive");
    expect(restoreShortfall(3, 3)).toBeNull();
    expect(restoreShortfall(3, 2)).toContain("1 task could not be restored");
    expect(restoreFailure(undefined, "Network down.")).toMatch(/Try again\.$/);
    expect(restoreFailure(503, "Busy.")).toMatch(/Try again\.$/);
    expect(restoreFailure(409, "Its project is archived.")).not.toContain("Try again");
    expect(restoredIds({ results: [{ task_id: "a" }, { task_id: "c" }] })).toEqual(["a", "c"]);
  });
});
