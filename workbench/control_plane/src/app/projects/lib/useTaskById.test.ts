/**
 * The one loader of a task panel that starts from an id (PK-1, task 2469).
 * My Tasks' detail and My Day's panel read through it.
 *
 * - The lanes come off the lens read first, then the project's, then none.
 * - A failed task read fails the load. A failed lanes, fields or tags read
 *   never does.
 * - `followMerge` opens the task a merged task went into, once.
 *
 * Mutation, run by hand (2026-11-09): drop the project lanes step, and the
 * "not in my lens" case fails. Drop the `followMerge` guard, and the
 * "My Tasks keeps its row" case fails.
 */
import { describe, expect, it, vi } from "vitest";

import type { FieldRow, StatusRow, TagRow, TaskRow } from "./api";
import { type TaskByIdIo, loadTaskExtras, loadTaskWithLanes, rootOf } from "./useTaskById";

const lane = (id: string): StatusRow => ({ id, name: id, project_id: "root", color: "", position: 0, category: "active" }) as StatusRow;
const task = (id: string, extra: Partial<TaskRow> = {}): TaskRow =>
  ({ id, title: id, project_id: "child", root_project_id: "root", ...extra }) as TaskRow;

function io(over: Partial<TaskByIdIo> = {}): TaskByIdIo {
  return {
    task: vi.fn(async (id: string) => task(id)),
    lensLanes: vi.fn(async () => [lane("lens")]),
    projectLanes: vi.fn(async () => [lane("project")]),
    fields: vi.fn(async () => [{ id: "f1" } as FieldRow]),
    tags: vi.fn(async () => [{ id: "t1" } as TagRow]),
    ...over,
  };
}

describe("loadTaskWithLanes", () => {
  it("reads the lanes off the lens first", async () => {
    const reads = io();
    const got = await loadTaskWithLanes("a", reads);
    expect(got.task.id).toBe("a");
    expect(got.statuses.map((s) => s.id)).toEqual(["lens"]);
    expect(reads.projectLanes).not.toHaveBeenCalled();
  });

  it("falls back to the project's lanes for a task not in my lens", async () => {
    const reads = io({ lensLanes: vi.fn(async () => Promise.reject(new Error("404"))) });
    const got = await loadTaskWithLanes("a", reads);
    expect(got.statuses.map((s) => s.id)).toEqual(["project"]);
    expect(reads.projectLanes).toHaveBeenCalledWith("root");
  });

  it("draws the body with no lanes when neither read answers", async () => {
    const reads = io({
      lensLanes: vi.fn(async () => Promise.reject(new Error("404"))),
      projectLanes: vi.fn(async () => Promise.reject(new Error("404"))),
    });
    expect((await loadTaskWithLanes("a", reads)).statuses).toEqual([]);
  });

  it("fails when the task read fails", async () => {
    const reads = io({ task: vi.fn(async () => Promise.reject(new Error("No such task"))) });
    await expect(loadTaskWithLanes("a", reads)).rejects.toThrow("No such task");
  });

  it("opens the task a merged task went into, once", async () => {
    const reads = io({
      task: vi.fn(async (id: string) => (id === "old" ? task("old", { merged_into_task_id: "new" }) : task(id))),
    });
    const got = await loadTaskWithLanes("old", reads, true);
    expect(got.task.id).toBe("new");
    expect(reads.lensLanes).toHaveBeenLastCalledWith("new");
  });

  it("My Tasks keeps its row: no option, no merge hop", async () => {
    const reads = io({ task: vi.fn(async (id: string) => task(id, { merged_into_task_id: "new" })) });
    expect((await loadTaskWithLanes("old", reads)).task.id).toBe("old");
    expect(reads.task).toHaveBeenCalledTimes(1);
  });
});

describe("loadTaskExtras", () => {
  it("reads the root project's fields and tags, each at best effort", async () => {
    const reads = io({ fields: vi.fn(async () => Promise.reject(new Error("403"))) });
    const got = await loadTaskExtras(task("a"), reads);
    expect(got.fields).toEqual([]);
    expect(got.tags.map((t) => t.id)).toEqual(["t1"]);
    expect(reads.tags).toHaveBeenCalledWith("root");
  });

  it("uses the task's own project when it names no root", () => {
    expect(rootOf({ root_project_id: "", project_id: "p" })).toBe("p");
  });
});
