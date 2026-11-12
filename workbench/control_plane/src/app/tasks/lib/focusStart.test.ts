import { describe, expect, it } from "vitest";

import { focusMoveLane } from "./focusStart";

/**
 * Which lane a focus start moves a task to (CC-4, MD-Q6). Mutations, run by
 * hand on 2026-11-07:
 *  - drop the NOT_STARTED test: the in_progress and done cases move, and fail.
 *  - drop the personal test: the personal-root case moves, and fails.
 *  - pick the last in_progress lane instead of `landingLane`'s first: the
 *    two-lane case fails.
 */
const LANES = [
  { id: "l-back", name: "Backlog", category: "backlog", position: 10 },
  { id: "l-todo", name: "To do", category: "todo", position: 20 },
  { id: "l-prog", name: "In progress", category: "in_progress", position: 30 },
  { id: "l-rev", name: "In review", category: "in_progress", position: 40 },
  { id: "l-done", name: "Done", category: "done", position: 50 },
];
const task = (statusCategory: string | undefined, over = {}) => ({
  projectId: "p1",
  rootProjectId: "root-co",
  statusId: "l-todo",
  statusCategory,
  ...over,
});

describe("focusMoveLane", () => {
  it("moves a company task in todo or backlog to the first in_progress lane", () => {
    expect(focusMoveLane(task("todo"), "root-me", [], LANES)?.id).toBe("l-prog");
    expect(focusMoveLane(task("backlog", { statusId: "l-back" }), "root-me", [], LANES)?.id).toBe("l-prog");
  });
  it("leaves a task that is in progress or later where it is", () => {
    expect(focusMoveLane(task("in_progress", { statusId: "l-prog" }), "root-me", [], LANES)).toBeNull();
    expect(focusMoveLane(task("done"), "root-me", [], LANES)).toBeNull();
  });
  it("leaves a personal task alone", () => {
    expect(focusMoveLane(task("todo", { projectId: "root-me", rootProjectId: "root-me" }), "root-me", [], LANES)).toBeNull();
    expect(focusMoveLane(task("todo", { projectId: "area-1" }), "root-me", ["area-1"], LANES)).toBeNull();
    expect(focusMoveLane(task("todo", { rootProjectId: "root-me" }), "root-me", [], LANES)).toBeNull();
  });
  it("does nothing when the board has no in_progress lane or the task has no stage", () => {
    expect(focusMoveLane(task("todo"), "root-me", [], LANES.slice(0, 2))).toBeNull();
    expect(focusMoveLane(task(undefined), "root-me", [], LANES)).toBeNull();
  });
});

/**
 * The Undo of a focus move (CC-4 fix round). Mutations, run by hand:
 *  - drop the `now.statusId !== movedToId` test: the "moved on" case writes, and fails.
 *  - drop `ifMatch`: the first case fails.
 */
import { vi } from "vitest";

vi.mock("./lens", () => ({
  lensGetItem: vi.fn(),
  lensSetStatusId: vi.fn(),
}));
import { ProjectsApiError } from "@/app/projects/lib/api";
import { lensGetItem, lensSetStatusId } from "./lens";
import { undoFocusMove } from "./focusStart";

const before = { statusId: "s-todo", workflowStage: "To do", statusCategory: "todo" };

describe("undoFocusMove", () => {
  it("writes the old status with If-Match from a fresh read", async () => {
    vi.mocked(lensGetItem).mockResolvedValue({ statusId: "s-prog", updatedAt: "v7" } as never);
    vi.mocked(lensSetStatusId).mockResolvedValue(undefined);
    const patch = vi.fn();
    expect(await undoFocusMove("t1", "s-prog", before, patch)).toBe("undone");
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "s-todo", { ifMatch: "v7" });
    expect(patch).toHaveBeenCalledWith(before);
  });
  it("leaves a task that a teammate moved on", async () => {
    vi.mocked(lensSetStatusId).mockClear();
    vi.mocked(lensGetItem).mockResolvedValue({ statusId: "s-review", updatedAt: "v8" } as never);
    const patch = vi.fn();
    expect(await undoFocusMove("t1", "s-prog", before, patch)).toBe("changed");
    expect(lensSetStatusId).not.toHaveBeenCalled();
    expect(patch).not.toHaveBeenCalled();
  });
  it("keeps a move that lands between the read and the write (412)", async () => {
    vi.mocked(lensGetItem).mockResolvedValue({ statusId: "s-prog", updatedAt: "v7" } as never);
    vi.mocked(lensSetStatusId).mockRejectedValue(new ProjectsApiError("changed", 412));
    const patch = vi.fn();
    expect(await undoFocusMove("t1", "s-prog", before, patch)).toBe("changed");
    expect(patch).not.toHaveBeenCalled();
  });
});
