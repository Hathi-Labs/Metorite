/**
 * UNDO-1 — a task edit undoes through one helper.
 *
 * Pinned: each field's inverse restores the BEFORE value (an absent value
 * becomes null), the inverse carries If-Match on the version the forward
 * write returned, a 412 shows one message and changes nothing else, and a
 * stale undo leaves the stack sane (redo is then inert).
 */
import { describe, expect, it, vi } from "vitest";

import { EMPTY_UNDO, record, stepBack, stepForward, type UndoEntry } from "@/lib/undo";

import {
  STALE_MESSAGE,
  moveBackPlan,
  recordAssigneesEdit,
  recordTaskEdit,
  type EditHooks,
  type EditIo,
} from "./taskUndo";

const before = {
  id: "t1",
  title: "Old",
  status_id: "s-done",
  due_at: "2026-11-05T00:00:00Z",
  start_date: null,
  estimate_mins: 60,
  assignees: ["a@x.io"],
  project_id: "p1",
};

function rig(over: Partial<EditIo> = {}) {
  const calls: { id: string; payload: unknown; ifMatch: string | null }[] = [];
  const io: EditIo = {
    patch: vi.fn(async (id, payload, ifMatch) => {
      calls.push({ id, payload, ifMatch });
      return { updated_at: `v${calls.length + 1}` };
    }),
    setAssignees: vi.fn(async () => ({})),
    read: vi.fn(async () => ({ assignees: ["b@x.io"], updated_at: "r" })),
    ...over,
  };
  let stack = EMPTY_UNDO;
  const notify = vi.fn();
  const hooks: EditHooks = {
    record: (e: UndoEntry) => {
      stack = record(stack, e);
    },
    notify,
    after: vi.fn(),
  };
  return { io, hooks, calls, notify, get stack() { return stack; } };
}

describe("recordTaskEdit", () => {
  it.each([
    ["status_id", { status_id: "s-open" }, { status_id: "s-done" }],
    ["title", { title: "New" }, { title: "Old" }],
    ["due_at", { due_at: null }, { due_at: "2026-11-05T00:00:00Z" }],
    ["start_date", { start_date: "2026-11-01" }, { start_date: null }],
    ["estimate_mins", { estimate_mins: 5 }, { estimate_mins: 60 }],
    ["project_id", { project_id: "p2" }, { project_id: "p1" }],
  ])("%s inverse restores the before value", async (_k, patch, inverse) => {
    const r = rig();
    recordTaskEdit(r.io, r.hooks, { before, patch, version: "v1", label: "edited Old" });
    const step = stepBack(r.stack)!;
    await step.entry.undo();
    expect(r.calls[0].payload).toEqual(inverse);
  });

  it("carries If-Match on the forward version, then on the version undo returned", async () => {
    const r = rig();
    recordTaskEdit(r.io, r.hooks, { before, patch: { title: "New" }, version: "v1", label: "x" });
    const back = stepBack(r.stack)!;
    await back.entry.undo();
    expect(r.calls[0].ifMatch).toBe("v1");
    const fwd = stepForward(back.stack)!;
    await fwd.entry.redo();
    expect(r.calls[1]).toMatchObject({ payload: { title: "New" }, ifMatch: "v2" });
  });

  it("a 412 on undo shows the message, writes nothing more, and makes redo inert", async () => {
    const stale = Object.assign(new Error("precondition"), { status: 412 });
    const r = rig({ patch: vi.fn(async () => { throw stale; }) });
    recordTaskEdit(r.io, r.hooks, { before, patch: { title: "New" }, version: "v1", label: "x" });
    const back = stepBack(r.stack)!;
    await expect(back.entry.undo()).resolves.toBeUndefined();
    expect(r.notify).toHaveBeenCalledWith(STALE_MESSAGE);
    expect(r.io.patch).toHaveBeenCalledTimes(1);
    await back.entry.redo();
    expect(r.io.patch).toHaveBeenCalledTimes(1);
  });

  it("any other failure is thrown so the stack keeps the step", async () => {
    const r = rig({ patch: vi.fn(async () => { throw new Error("boom"); }) });
    recordTaskEdit(r.io, r.hooks, { before, patch: { title: "New" }, version: null, label: "x" });
    await expect(stepBack(r.stack)!.entry.undo()).rejects.toThrow("boom");
    expect(r.notify).not.toHaveBeenCalled();
  });

  it("returns an undo that runs once for the toast and Ctrl+Z together", async () => {
    const r = rig();
    const undo = recordTaskEdit(r.io, r.hooks, { before, patch: { title: "New" }, version: "v1", label: "x" });
    await undo();
    await stepBack(r.stack)!.entry.undo();
    expect(r.io.patch).toHaveBeenCalledTimes(1);
  });
});

describe("recordAssigneesEdit", () => {
  it("puts the prior list back while the list is still what undo set", async () => {
    const r = rig({ read: vi.fn(async () => ({ assignees: ["c@x.io"] })) });
    recordAssigneesEdit(r.io, r.hooks, { id: "t1", before: ["a@x.io"], next: ["c@x.io"], label: "x" });
    await stepBack(r.stack)!.entry.undo();
    expect(r.io.setAssignees).toHaveBeenCalledWith("t1", ["a@x.io"]);
  });

  it("refuses with the stale message when somebody changed the list since", async () => {
    const r = rig();
    recordAssigneesEdit(r.io, r.hooks, { id: "t1", before: ["a@x.io"], next: ["c@x.io"], label: "x" });
    await stepBack(r.stack)!.entry.undo();
    expect(r.io.setAssignees).not.toHaveBeenCalled();
    expect(r.notify).toHaveBeenCalledWith(STALE_MESSAGE);
  });
});

describe("moveBackPlan", () => {
  it("groups moved tasks by source project and maps status back", () => {
    const plan = moveBackPlan(
      [
        { id: "a", project_id: "p1", status_id: "s1" },
        { id: "b", project_id: "p1", status_id: "s2" },
        { id: "c", project_id: "p9", status_id: "s3" },
      ],
      { a: "n1", b: "n2", c: "n3" },
    );
    expect(plan).toEqual([
      { projectId: "p1", taskIds: ["a", "b"], statusMap: { n1: "s1", n2: "s2" } },
      { projectId: "p9", taskIds: ["c"], statusMap: { n3: "s3" } },
    ]);
  });
});
