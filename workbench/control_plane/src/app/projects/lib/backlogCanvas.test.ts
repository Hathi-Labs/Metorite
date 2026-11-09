/**
 * WS-39 SP-3a: the rules of the Backlog canvas (`backlogCanvas.ts`). The runner
 * has no DOM, so each rule of the canvas is a pure function here, and the look
 * is a capture.
 */
import { describe, expect, it, vi } from "vitest";

import type { UndoEntry } from "@/lib/undo";

import type { CycleRow } from "./api";
import {
  BACKLOG,
  NOW_IN_BACKLOG,
  OPEN_CATEGORIES,
  type MoveIo,
  type Place,
  applyResults,
  backlogQuery,
  countWords,
  cycleDates,
  cyclePlace,
  moveLabel,
  moveMany,
  moveOne,
  moveSteps,
  moveSurface,
  moveTargets,
  moveTitle,
  planDefaults,
  planProblem,
  plannedCycles,
  probeSprints,
  recordMove,
  refusalSummary,
  runningCycle,
} from "./backlogCanvas";

const cycle = (over: Partial<CycleRow>): CycleRow => ({
  id: "c",
  project_id: "p",
  name: "Sprint",
  goal: null,
  starts_on: "2026-11-10",
  ends_on: "2026-11-16",
  state: "planned",
  updated_at: null,
  tasks: 0,
  hidden: 0,
  ...over,
});

/** A fake server: where each task sits, and the refusals to send. */
function fakeServer(start: Record<string, string | null>) {
  const at = new Map(Object.entries(start));
  const refuse = new Map<string, Error>();
  const calls: string[] = [];
  const io: MoveIo = {
    add: async (cycleId, taskId) => {
      calls.push(`add ${cycleId} ${taskId}`);
      const no = refuse.get(`add ${cycleId} ${taskId}`) ?? refuse.get(`add ${cycleId}`);
      if (no) throw no;
      if (at.get(taskId)) throw Object.assign(new Error("This task is in another cycle."), { status: 409 });
      at.set(taskId, cycleId);
    },
    remove: async (cycleId, taskId) => {
      calls.push(`remove ${cycleId} ${taskId}`);
      const no = refuse.get(`remove ${cycleId} ${taskId}`) ?? refuse.get(`remove ${cycleId}`);
      if (no) throw no;
      if (at.get(taskId) !== cycleId) throw Object.assign(new Error("This task is not in this cycle."), { status: 404 });
      at.set(taskId, null);
    },
  };
  return { io, at, refuse, calls };
}

const recorder = () => {
  const recorded: UndoEntry[] = [];
  const notes: string[] = [];
  const hooks = {
    record: (entry: UndoEntry) => recorded.push(entry),
    notify: (message: string) => notes.push(message),
    after: vi.fn(),
    placed: vi.fn(),
  };
  return { recorded, notes, hooks };
};

describe("the cycles at the top", () => {
  it("shows the planned cycles only, soonest first, then by name", () => {
    const rows = [
      cycle({ id: "late", name: "B", starts_on: "2026-12-01" }),
      cycle({ id: "run", name: "Now", state: "active", starts_on: "2026-11-01" }),
      cycle({ id: "soon2", name: "Z", starts_on: "2026-11-10" }),
      cycle({ id: "done", name: "Old", state: "closed", starts_on: "2026-10-01" }),
      cycle({ id: "soon1", name: "A", starts_on: "2026-11-10" }),
    ];
    expect(plannedCycles(rows).map((c) => c.id)).toEqual(["soon1", "soon2", "late"]);
    expect(runningCycle(rows)?.id).toBe("run");
    expect(runningCycle([])).toBeNull();
  });

  it("names the count of each cycle, and the tasks the reader cannot see", () => {
    expect(countWords(0)).toBe("No tasks");
    expect(countWords(1)).toBe("1 task");
    expect(countWords(4)).toBe("4 tasks");
    expect(countWords(4, 2)).toBe("4 tasks, 2 hidden");
    expect(countWords(0, 3)).toBe("No tasks, 3 hidden");
  });

  it("writes the two dates as a short range a reader in any zone reads the same", () => {
    expect(cycleDates({ starts_on: "2026-11-10", ends_on: "2026-11-16" })).toBe("10 Nov – 16 Nov");
    expect(cycleDates({ starts_on: null, ends_on: null })).toBe("");
  });
});

describe("the backlog read", () => {
  it("asks the one task list for the open tasks in no open cycle, most pressing first", () => {
    const q = backlogQuery("node-1");
    expect(q).toMatchObject({
      project_id: "node-1",
      include_subtree: true,
      cycle: "none",
      top_level: true,
      sort: "importance",
      direction: "desc",
      page: 1,
      page_size: 100,
    });
    expect(String(q.status_category).split(",")).toEqual([...OPEN_CATEGORIES]);
    expect(OPEN_CATEGORIES).not.toContain("done");
    expect(OPEN_CATEGORIES).not.toContain("cancelled");
    expect(backlogQuery("node-1", 3).page).toBe(3);
  });
});

describe("the flag", () => {
  it("reads a 404 of the cycle list as the flag off", async () => {
    const off = await probeSprints(() => Promise.reject(Object.assign(new Error("Not Found"), { status: 404 })));
    expect(off).toEqual({ on: false });
    const on = await probeSprints(() => Promise.resolve({ cycles: [cycle({ id: "a" })] }));
    expect(on).toEqual({ on: true, cycles: [cycle({ id: "a" })] });
  });

  it("does not read another failure as the flag off", async () => {
    await expect(
      probeSprints(() => Promise.reject(Object.assign(new Error("Bad gateway"), { status: 502 }))),
    ).rejects.toThrow("Bad gateway");
  });
});

describe("a move", () => {
  const A = cyclePlace("A");
  const B = cyclePlace("B");

  it("adds from the backlog, removes to it, and removes then adds between cycles", () => {
    expect(moveSteps(BACKLOG, A)).toEqual([{ op: "add", cycleId: "A" }]);
    expect(moveSteps(A, BACKLOG)).toEqual([{ op: "remove", cycleId: "A" }]);
    expect(moveSteps(A, B)).toEqual([
      { op: "remove", cycleId: "A" },
      { op: "add", cycleId: "B" },
    ]);
    expect(moveSteps(A, A)).toEqual([]);
    expect(moveSteps(BACKLOG, BACKLOG)).toEqual([]);
  });

  it("moves a task into a cycle and back", async () => {
    const s = fakeServer({ t1: null });
    expect(await moveOne(s.io, "t1", BACKLOG, A)).toEqual({ taskId: "t1", ok: true, at: A });
    expect(s.at.get("t1")).toBe("A");
    expect(await moveOne(s.io, "t1", A, BACKLOG)).toEqual({ taskId: "t1", ok: true, at: BACKLOG });
    expect(s.at.get("t1")).toBeNull();
  });

  it("leaves a refused task where it was, with the server's reason", async () => {
    const s = fakeServer({ t1: "other" });
    const out = await moveOne(s.io, "t1", BACKLOG, A);
    expect(out).toEqual({ taskId: "t1", ok: false, at: BACKLOG, reason: "This task is in another cycle." });
    expect(s.at.get("t1")).toBe("other");
  });

  it("says that the task is in the backlog when the add fails after the remove", async () => {
    const s = fakeServer({ t1: "A" });
    s.refuse.set("add B", Object.assign(new Error("This cycle is closed."), { status: 409 }));
    const out = await moveOne(s.io, "t1", A, B);
    expect(out.ok).toBe(false);
    expect(out.at).toEqual(BACKLOG);
    expect(out.ok ? "" : out.reason).toBe(`This cycle is closed. ${NOW_IN_BACKLOG}`);
    expect(s.calls).toEqual(["remove A t1", "add B t1"]);
  });

  it("keeps the task in its cycle when the remove is refused, and sends no add", async () => {
    const s = fakeServer({ t1: "A" });
    s.refuse.set("remove A", Object.assign(new Error("This cycle is closed."), { status: 409 }));
    const out = await moveOne(s.io, "t1", A, B);
    expect(out).toEqual({ taskId: "t1", ok: false, at: A, reason: "This cycle is closed." });
    expect(s.calls).toEqual(["remove A t1"]);
  });

  it("uses plain words when the refusal has none", async () => {
    const s = fakeServer({ t1: null });
    s.refuse.set("add A", new Error(""));
    const out = await moveOne(s.io, "t1", BACKLOG, A);
    expect(out.ok ? "" : out.reason).toBe("The move did not go through. Try again.");
  });

  it("moves many tasks one after another, and draws each where the server has it", async () => {
    const s = fakeServer({ t1: null, t2: "other", t3: null });
    const results = await moveMany(
      s.io,
      [
        { taskId: "t1", from: BACKLOG },
        { taskId: "t2", from: BACKLOG },
        { taskId: "t3", from: BACKLOG },
      ],
      A,
    );
    expect(results.map((r) => r.ok)).toEqual([true, false, true]);
    expect(s.calls).toEqual(["add A t1", "add A t2", "add A t3"]);
    const shown = applyResults(new Map<string, Place>([["t2", A]]), results);
    expect(shown.get("t1")).toEqual(A);
    expect(shown.get("t2")).toEqual(BACKLOG);
    expect(refusalSummary(results)).toBe("This task is in another cycle.");
    expect(refusalSummary(results.filter((r) => r.ok))).toBeNull();
    expect(
      refusalSummary([
        { taskId: "a", ok: false, at: BACKLOG, reason: "One." },
        { taskId: "b", ok: false, at: BACKLOG, reason: "Two." },
      ]),
    ).toBe("One. (2 tasks did not move.)");
  });

  it("names the verb, the count and the place", () => {
    expect(moveTitle(1, "Sprint 3")).toBe("Moved 1 task to Sprint 3");
    expect(moveTitle(3, null)).toBe("Moved 3 tasks to the backlog");
    expect(moveLabel(2, "Sprint 3")).toBe("moved 2 tasks to Sprint 3");
  });
});

describe("Undo", () => {
  const A = cyclePlace("A");
  const B = cyclePlace("B");

  it("records one entry, and Undo puts each task where it was", async () => {
    const s = fakeServer({ t1: null, t2: "B" });
    const { recorded, hooks } = recorder();
    const moved = [
      { taskId: "t1", from: BACKLOG },
      { taskId: "t2", from: B },
    ];
    await moveMany(s.io, moved, A);
    expect(s.at.get("t1")).toBe("A");
    expect(s.at.get("t2")).toBe("A");

    const entry = recordMove(s.io, hooks, moved, A, moveLabel(2, "A"));
    expect(recorded).toEqual([entry]);
    expect(entry.label).toBe("moved 2 tasks to A");

    await entry.undo();
    expect(s.at.get("t1")).toBeNull();
    expect(s.at.get("t2")).toBe("B");
    expect(hooks.after).toHaveBeenCalledTimes(1);
    expect(hooks.placed).toHaveBeenLastCalledWith([
      { taskId: "t1", ok: true, at: BACKLOG },
      { taskId: "t2", ok: true, at: B },
    ]);

    await entry.redo();
    expect(s.at.get("t1")).toBe("A");
    expect(s.at.get("t2")).toBe("A");
  });

  it("sends one Undo when a click and Ctrl+Z come together", async () => {
    const s = fakeServer({ t1: null });
    const { hooks } = recorder();
    const moved = [{ taskId: "t1", from: BACKLOG }];
    await moveMany(s.io, moved, A);
    const entry = recordMove(s.io, hooks, moved, A, "moved 1 task to A");
    s.calls.length = 0;
    await Promise.all([entry.undo(), entry.undo()]);
    expect(s.calls).toEqual(["remove A t1"]);
    await entry.undo();
    expect(s.calls).toEqual(["remove A t1"]);
  });

  it("says why an Undo was refused, and a dead entry writes nothing again", async () => {
    const s = fakeServer({ t1: null });
    const { notes, hooks } = recorder();
    const moved = [{ taskId: "t1", from: BACKLOG }];
    await moveMany(s.io, moved, A);
    const entry = recordMove(s.io, hooks, moved, A, "moved 1 task to A");
    s.refuse.set("remove A", Object.assign(new Error("This cycle is closed."), { status: 409 }));
    await entry.undo();
    expect(notes).toEqual(["This cycle is closed."]);
    expect(s.at.get("t1")).toBe("A");
    expect(hooks.after).not.toHaveBeenCalled();
    s.calls.length = 0;
    await entry.redo();
    await entry.undo();
    expect(s.calls).toEqual([]);
  });
});

describe("the phone rule and the targets", () => {
  it("drags on a desktop, and opens a sheet with no drag on a phone", () => {
    expect(moveSurface(false)).toEqual({ drag: true, sheet: false });
    expect(moveSurface(true)).toEqual({ drag: false, sheet: true });
  });

  it("offers each planned cycle, then the backlog, and marks where the tasks are", () => {
    const cycles = [
      cycle({ id: "B", name: "Sprint 2", starts_on: "2026-11-17" }),
      cycle({ id: "A", name: "Sprint 1", starts_on: "2026-11-10" }),
      cycle({ id: "X", name: "Running", state: "active" }),
    ];
    const targets = moveTargets(cycles, [BACKLOG, BACKLOG]);
    expect(targets.map((t) => t.label)).toEqual(["Sprint 1", "Sprint 2", "Backlog"]);
    expect(targets.map((t) => t.here)).toEqual([false, false, true]);
    expect(moveTargets(cycles, [BACKLOG, cyclePlace("A")]).every((t) => !t.here)).toBe(true);
    expect(moveTargets(cycles, [cyclePlace("A")])[0].here).toBe(true);
  });
});

describe("Plan a sprint", () => {
  it("starts with the next number, today and one week", () => {
    expect(planDefaults(new Date(2026, 10, 9), 2)).toEqual({
      name: "Sprint 3",
      starts_on: "2026-11-09",
      ends_on: "2026-11-15",
    });
    expect(planDefaults(new Date(2026, 11, 29), 0).ends_on).toBe("2027-01-04");
  });

  it("says what to fix before it sends, as the server would", () => {
    const ok = { name: "Sprint 1", starts_on: "2026-11-09", ends_on: "2026-11-15" };
    expect(planProblem(ok)).toBeNull();
    expect(planProblem({ ...ok, name: "  " })).toBe("Write a name for the sprint.");
    expect(planProblem({ ...ok, name: "x".repeat(121) })).toMatch(/at most 120/);
    expect(planProblem({ ...ok, ends_on: "2026-11-01" })).toMatch(/ends before it starts/);
    expect(planProblem({ ...ok, ends_on: "2027-12-01" })).toMatch(/at most 366 days/);
    expect(planProblem({ ...ok, starts_on: "" })).toBe("Pick a start date and an end date.");
  });
});
