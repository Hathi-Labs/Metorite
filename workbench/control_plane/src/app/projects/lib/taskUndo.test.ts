/**
 * UNDO-1 — a task edit undoes through one helper.
 *
 * Pinned: each field's inverse restores the BEFORE value (an absent value
 * becomes null), the inverse carries If-Match on the task's version NOW, a
 * 412 shows one message and changes nothing else, and a stale undo leaves the
 * stack sane (redo is then inert). Fix round 1 adds the version chain, the
 * custom field merge, the move plan and the doors' wiring.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { EMPTY_UNDO, record, stepBack, stepForward, type UndoEntry } from "@/lib/undo";

import {
  STALE_MESSAGE,
  MOVE_NO_UNDO,
  type MovedRow,
  createVersionLedger,
  moveUndoPlan,
  recordAssigneesEdit,
  recordTaskEdit,
  taskInverse,
  type EditHooks,
  type EditIo,
} from "./taskUndo";

type Row = Record<string, unknown>;

/**
 * A task store that keeps the server's version rule
 * (`core.precondition_holds`). A token passes when it equals `updated_at`, or
 * lies from `fields_at` up to `updated_at`. A field write moves both stamps
 * (trigger 304). A comment or an assignee moves `updated_at` alone.
 * `custom_fields` MERGES, and a null clears a key (`apply_values`).
 */
function fakeServer(rows: Record<string, Row>) {
  let clock = 0;
  const stamp = () => new Date(Date.UTC(2026, 10, 4) + ++clock * 1000).toISOString();
  const state = new Map<string, { row: Row; updated_at: string; fields_at: string }>();
  for (const [id, row] of Object.entries(rows)) {
    const at = stamp();
    state.set(id, { row: structuredClone(row), updated_at: at, fields_at: at });
  }
  const holds = (id: string, token: string | null) => {
    if (!token) return true;
    const t = state.get(id)!;
    const at = Date.parse(token);
    return (
      token === t.updated_at ||
      (Date.parse(t.fields_at) <= at && at <= Date.parse(t.updated_at))
    );
  };
  const stale = () => Object.assign(new Error("precondition"), { status: 412 });
  const fieldWrite = (id: string, payload: Row) => {
    const t = state.get(id)!;
    for (const [key, value] of Object.entries(payload)) {
      if (key !== "custom_fields") {
        t.row[key] = value;
        continue;
      }
      const merged = { ...((t.row.custom_fields as Row | undefined) ?? {}) };
      for (const [k, v] of Object.entries(value as Row)) {
        if (v === null) delete merged[k];
        else merged[k] = v;
      }
      t.row.custom_fields = merged;
    }
    t.updated_at = t.fields_at = stamp();
    return { ...structuredClone(t.row), updated_at: t.updated_at };
  };
  const io: EditIo = {
    patch: vi.fn(async (id: string, payload: Row, ifMatch: string | null) => {
      if (!holds(id, ifMatch)) throw stale();
      return fieldWrite(id, payload);
    }),
    setAssignees: vi.fn(async () => ({})),
    read: vi.fn(async (id: string) => ({
      ...structuredClone(state.get(id)!.row),
      updated_at: state.get(id)!.updated_at,
    })),
    versions: createVersionLedger(),
  };
  return {
    io,
    row: (id: string) => state.get(id)!.row,
    /** Somebody else changes a field of the task. */
    otherEdit: (id: string, payload: Row) => void fieldWrite(id, payload),
    /** A comment lands: the stamp moves, and no field changes. */
    bump: (id: string) => {
      state.get(id)!.updated_at = stamp();
    },
  };
}

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
    versions: createVersionLedger(),
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

/** A door's forward write on `srv`, then its record, as `useTaskUndo` does. */
async function forward(
  srv: ReturnType<typeof fakeServer>,
  r: ReturnType<typeof rig>,
  id: string,
  patch: Row,
) {
  const before = structuredClone(srv.row(id));
  const fresh = (await srv.io.patch(id, patch, null)) as { updated_at: string };
  return recordTaskEdit(srv.io, r.hooks, {
    before: { id, ...before },
    patch,
    version: fresh.updated_at,
    label: "x",
  });
}

describe("the version chain (fix round 1, finding 1)", () => {
  it("two steps on ONE task undo twice and redo twice", async () => {
    const srv = fakeServer({ t1: { status_id: "A" } });
    const r = rig(srv.io);
    await forward(srv, r, "t1", { status_id: "B" });
    await forward(srv, r, "t1", { status_id: "C" });

    let step = stepBack(r.stack)!;
    await step.entry.undo();
    expect(srv.row("t1").status_id).toBe("B");
    step = stepBack(step.stack)!;
    await step.entry.undo();
    expect(srv.row("t1").status_id).toBe("A");
    step = stepForward(step.stack)!;
    await step.entry.redo();
    expect(srv.row("t1").status_id).toBe("B");
    step = stepForward(step.stack)!;
    await step.entry.redo();
    expect(srv.row("t1").status_id).toBe("C");
    expect(r.notify).not.toHaveBeenCalled();
  });

  it("an edit by somebody else in between still answers 412", async () => {
    const srv = fakeServer({ t1: { status_id: "A", title: "Old" } });
    const r = rig(srv.io);
    await forward(srv, r, "t1", { status_id: "B" });
    srv.otherEdit("t1", { title: "Theirs" });
    await stepBack(r.stack)!.entry.undo();
    expect(r.notify).toHaveBeenCalledWith(STALE_MESSAGE);
    expect(srv.row("t1")).toEqual({ status_id: "B", title: "Theirs" });
  });

  it("a comment in between is not a change: undo still runs", async () => {
    const srv = fakeServer({ t1: { status_id: "A" } });
    const r = rig(srv.io);
    await forward(srv, r, "t1", { status_id: "B" });
    srv.bump("t1");
    await stepBack(r.stack)!.entry.undo();
    expect(srv.row("t1").status_id).toBe("A");
    expect(r.notify).not.toHaveBeenCalled();
  });

  it("the ledger keeps the later stamp, to the microsecond", () => {
    const v = createVersionLedger();
    v.note("t", "2026-11-04T10:00:00.000002+00:00");
    v.note("t", "2026-11-04T10:00:00.000001+00:00");
    expect(v.get("t")).toBe("2026-11-04T10:00:00.000002+00:00");
    // The server omits a zero fraction. That stamp is still the later one.
    v.note("t", "2026-11-04T10:00:01+00:00");
    expect(v.get("t")).toBe("2026-11-04T10:00:01+00:00");
    v.note("t", null);
    expect(v.get("t")).toBe("2026-11-04T10:00:01+00:00");
  });
});

describe("custom fields merge on the server (fix round 1, finding 2)", () => {
  it("a custom field filled from empty clears again on undo", async () => {
    const srv = fakeServer({ t1: { custom_fields: { size: "M" } } });
    const r = rig(srv.io);
    await forward(srv, r, "t1", { custom_fields: { owner: "Ana" } });
    await forward(srv, r, "t1", { custom_fields: { size: "L" } });
    expect(srv.row("t1").custom_fields).toEqual({ size: "L", owner: "Ana" });

    let step = stepBack(r.stack)!;
    await step.entry.undo();
    expect(srv.row("t1").custom_fields).toEqual({ size: "M", owner: "Ana" });
    step = stepBack(step.stack)!;
    await step.entry.undo();
    expect(srv.row("t1").custom_fields).toEqual({ size: "M" });
    step = stepForward(step.stack)!;
    await step.entry.redo();
    expect(srv.row("t1").custom_fields).toEqual({ size: "M", owner: "Ana" });
  });

  it("the custom field inverse names only the keys the edit sent", () => {
    const row = { id: "t1", title: "T", custom_fields: { a: 1, b: false } };
    expect(taskInverse(row, { custom_fields: { b: true, c: "x" } })).toEqual({
      custom_fields: { b: false, c: null },
    });
    expect(taskInverse({ id: "t2" }, { custom_fields: { a: 2 }, title: "N" })).toEqual({
      custom_fields: { a: null },
      title: null,
    });
  });
});

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

describe("moveUndoPlan (fix round 1, finding 3)", () => {
  const row = (id: string, project_id: string, status_id: string, over: Partial<MovedRow> = {}): MovedRow => ({
    id,
    project_id,
    root_project_id: "space",
    status_id,
    type_id: "bug",
    task_number: Number(id.charCodeAt(0)),
    completed_at: null,
    ...over,
  });
  const clean = { dropped_fields: [] as string[] };

  it("each task goes home to its OWN prior status, even from one shared lane", () => {
    // A bulk move from To do and In progress into one destination lane.
    const before = [row("a", "p1", "todo"), row("b", "p1", "doing"), row("c", "p9", "todo")];
    const after = { a: row("a", "p2", "lane"), b: row("b", "p2", "lane"), c: row("c", "p2", "lane") };
    const plan = moveUndoPlan(["a", "b", "c"], before, after, clean);
    expect(plan).toEqual({
      ok: true,
      calls: [
        { projectId: "p1", taskIds: ["a"], statusMap: { lane: "todo" } },
        { projectId: "p1", taskIds: ["b"], statusMap: { lane: "doing" } },
        { projectId: "p9", taskIds: ["c"], statusMap: { lane: "todo" } },
      ],
    });
  });

  it("tasks from one project share a call while their maps agree", () => {
    const before = [row("a", "p1", "s1"), row("b", "p1", "s2"), row("c", "p1", "s1")];
    const after = { a: row("a", "p2", "n1"), b: row("b", "p2", "n2"), c: row("c", "p2", "n1") };
    expect(moveUndoPlan(["a", "b", "c"], before, after, clean)).toEqual({
      ok: true,
      calls: [{ projectId: "p1", taskIds: ["a", "b", "c"], statusMap: { n1: "s1", n2: "s2" } }],
    });
  });

  it.each([
    ["carried subtasks", { subtask_ids: ["k"] }, {}, MOVE_NO_UNDO.subtasks],
    ["a dropped field", { dropped_fields: ["Size"] }, {}, MOVE_NO_UNDO.drops(["Size"])],
    ["another space", {}, { root_project_id: "other" }, MOVE_NO_UNDO.space],
    ["a new task number", {}, { task_number: 999 }, MOVE_NO_UNDO.space],
    ["a cleared type", {}, { type_id: null }, MOVE_NO_UNDO.space],
    ["a new done date", {}, { completed_at: "2026-11-04T00:00:00Z" }, MOVE_NO_UNDO.done],
  ])("offers no Undo after %s, and says why", (_case, answer, landed, reason) => {
    const plan = moveUndoPlan(
      ["a"],
      [row("a", "p1", "s1")],
      { a: row("a", "p2", "s1", landed) },
      { ...clean, ...answer },
    );
    expect(plan).toEqual({ ok: false, reason });
  });

  it("offers no Undo when a task could not be read", () => {
    const plan = moveUndoPlan(["a", "b"], [row("a", "p1", "s1")], { a: row("a", "p2", "s1") }, clean);
    expect(plan).toEqual({ ok: false, reason: MOVE_NO_UNDO.unread });
  });

  it("every reason says what happened and what to do next", () => {
    for (const line of [
      MOVE_NO_UNDO.subtasks,
      MOVE_NO_UNDO.drops(["Size"]),
      MOVE_NO_UNDO.space,
      MOVE_NO_UNDO.done,
      MOVE_NO_UNDO.unread,
    ]) {
      expect(line).toMatch(/^Undo is off for this move: /);
      expect(line).toMatch(/Use Move to project to send/);
    }
  });
});

describe("the doors wire the helper (source)", () => {
  const SRC = fileURLToPath(new URL("..", import.meta.url));
  const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8");

  it("a move offers Undo through moveUndoPlan, and shows its reason otherwise", () => {
    const page = read("page.tsx");
    expect(page).toMatch(/moveUndoPlan\(/);
    expect(page).toMatch(/description: way\.reason/);
  });

  it("the My tasks board write records Undo, as the project board does (finding 4)", () => {
    const src = read("components/MyTasksSpace.tsx");
    const drop = src.slice(src.indexOf("async function drop("), src.indexOf("async function exportCsv("));
    expect(drop).toMatch(/const fresh = await projectsApi\.patchTask\(task\.id, plan\.patch\)/);
    expect(drop).toMatch(/taskUndo\.edited\(task, plan\.patch, fresh,/);
  });
});
