/**
 * STU-D2b-1 fence: the inline edit of a Table cell.
 *
 * The store has no React, as `boardMove.ts` has none, so it runs here in the
 * node environment against a fake door.
 *
 * Mutation 1: skip the restore in `commit` (leave `shown` as the member set
 * it after a refusal). "a refusal puts the old value back" turns red.
 * Mutation 2: let a commit on a cell in flight call the door. "a second
 * commit on a cell in flight" turns red.
 * Mutation 3: drop `invalidate(doorPrefix(door))`. "an accepted edit drops the
 * cached reads" turns red.
 * Mutation 4: let `isInlineEditable` ignore `computed`. "a computed field never edits" turns red.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, put, peek } from "@/lib/dataCache";
import type { ActionResult, StudioDataDoor } from "./dataDoor";
import { sourceKey } from "./dataDoor";
import type { FieldSpec } from "./fieldCheck";
import { cellId, computedKeys, createEditor, isInlineEditable } from "./inlineEdit";

beforeEach(() => clearAll());

const FIELDS: FieldSpec[] = [
  { key: "title", type: "text", label: "Title", required: true },
  { key: "qty", type: "number", label: "Pallets" },
  { key: "done", type: "checkbox", label: "Done" },
  { key: "status", type: "select", label: "Status", options: ["Planned", "Closed"] },
  { key: "notes", type: "longText", label: "Notes" },
  { key: "total", type: "number", label: "Total" },
];
const UPDATE = "object:visit.update";

type Run = (action: string, args?: Record<string, unknown>) => Promise<ActionResult>;
function fakeDoor(run: Run): StudioDataDoor & { run: ReturnType<typeof vi.fn> } {
  return { scope: "t:live", read: async () => [], run: vi.fn(run) };
}
function pendingDoor() {
  const open: Array<(r: ActionResult) => void> = [];
  const door = fakeDoor(() => new Promise<ActionResult>((r) => { open.push(r); }));
  return { door, settle: (r: ActionResult) => open.splice(0).forEach((fn) => fn(r)) };
}
const ok: ActionResult = { ok: true };

describe("a commit", () => {
  it("shows the new value at once, before the door answers", () => {
    const { door } = pendingDoor();
    const ed = createEditor(door, UPDATE, FIELDS);
    void ed.commit("v1", "title", "New", "Old");
    expect(ed.getState().shown).toEqual({ v1: { title: "New" } });
    expect(ed.getState().pending.has(cellId("v1", "title"))).toBe(true);
    expect(ed.getState().editing).toBeNull();
  });
  it("makes exactly one update call, with the id and one key", async () => {
    const door = fakeDoor(async () => ok);
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "qty", "12", 3);
    expect(door.run).toHaveBeenCalledTimes(1);
    expect(door.run).toHaveBeenCalledWith(UPDATE, { id: "v1", data: { qty: 12 } });
  });
  it("sends null for a blank control on a field that is not required", async () => {
    const door = fakeDoor(async () => ok);
    await createEditor(door, UPDATE, FIELDS).commit("v1", "qty", "", 3);
    expect(door.run).toHaveBeenCalledWith(UPDATE, { id: "v1", data: { qty: null } });
  });
  it("sends a tick as a boolean", async () => {
    const door = fakeDoor(async () => ok);
    await createEditor(door, UPDATE, FIELDS).commit("v1", "done", true, false);
    expect(door.run).toHaveBeenCalledWith(UPDATE, { id: "v1", data: { done: true } });
  });
  it("sends nothing for a value that did not change", async () => {
    const door = fakeDoor(async () => ok);
    const ed = createEditor(door, UPDATE, FIELDS);
    ed.open("v1", "title");
    await ed.commit("v1", "title", "Same", "Same");
    expect(door.run).not.toHaveBeenCalled();
    expect(ed.getState().editing).toBeNull();
  });
  it("sends nothing for a blank required field, and says why", async () => {
    const door = fakeDoor(async () => ok);
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "title", "", "Old");
    expect(door.run).not.toHaveBeenCalled();
    expect(ed.getState().alert).toBe("Title: This field is required.");
  });
  it("sends nothing for a value that fails the client check", async () => {
    const door = fakeDoor(async () => ok);
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "qty", "abc", 3);
    expect(door.run).not.toHaveBeenCalled();
    expect(ed.getState().alert).toMatch(/^Pallets: /);
    expect(ed.getState().shown).toEqual({});
  });
  it("a second commit on a cell in flight does nothing", async () => {
    const { door, settle } = pendingDoor();
    const ed = createEditor(door, UPDATE, FIELDS);
    const first = ed.commit("v1", "title", "A", "Old");
    await ed.commit("v1", "title", "B", "Old");
    expect(door.run).toHaveBeenCalledTimes(1);
    expect(ed.getState().shown).toEqual({ v1: { title: "A" } });
    settle(ok);
    await first;
  });
  it("another cell of the same row is free while one is in flight", async () => {
    const { door, settle } = pendingDoor();
    const ed = createEditor(door, UPDATE, FIELDS);
    const first = ed.commit("v1", "title", "A", "Old");
    const second = ed.commit("v1", "qty", "5", 1);
    expect(door.run).toHaveBeenCalledTimes(2);
    settle(ok);
    await Promise.all([first, second]);
  });
});

describe("a refusal", () => {
  it("puts the old value back and shows the field sentence under the label", async () => {
    const door = fakeDoor(async () => ({
      ok: false, message: "That did not save.",
      data: { fieldErrors: { title: "A visit with this title exists. Use another title." } },
    }));
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "title", "Duplicate", "Old");
    expect(ed.getState().shown).toEqual({});
    expect(ed.getState().alert).toBe("Title: A visit with this title exists. Use another title.");
    expect(ed.getState().pending.size).toBe(0);
  });
  it("a refusal puts the old value back: the value that an earlier edit set returns", async () => {
    let n = 0;
    const door = fakeDoor(async () => (++n === 1 ? ok : { ok: false, message: "No." }));
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "qty", "5", 1);
    await ed.commit("v1", "qty", "9", 5);
    expect(ed.getState().shown).toEqual({ v1: { qty: 5 } });
  });
  it("shows the rule by name", async () => {
    const door = fakeDoor(async () => ({
      ok: false, message: "That did not save.",
      data: { rule: "close_needs_done", message: "A visit closes only when it is done." },
    }));
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "status", "Closed", "Planned");
    expect(ed.getState().alert).toBe("Refused by close_needs_done: A visit closes only when it is done.");
    expect(ed.getState().shown).toEqual({});
  });
  it("a refusal with only a message shows that message", async () => {
    const door = fakeDoor(async () => ({ ok: false, message: "This app is busy now. Try again in a moment." }));
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "done", true, false);
    expect(ed.getState().alert).toBe("This app is busy now. Try again in a moment.");
    expect(ed.getState().shown).toEqual({});
  });
  it("a throw restores and shows a plain sentence, never the error text", async () => {
    const door = fakeDoor(async () => { throw new Error("ECONNRESET at 10.0.0.4"); });
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "title", "A", "Old");
    expect(ed.getState().shown).toEqual({});
    expect(ed.getState().alert).toBe("That did not work. Try again.");
  });
  it("the cell is free again after a refusal", async () => {
    const door = fakeDoor(async () => ({ ok: false, message: "No." }));
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "title", "A", "Old");
    await ed.commit("v1", "title", "B", "Old");
    expect(door.run).toHaveBeenCalledTimes(2);
  });
});

describe("an accepted edit", () => {
  it("drops the cached reads of the door", async () => {
    const door = fakeDoor(async () => ok);
    const key = sourceKey(door, { action: "object:visit.list" });
    put(key, [{ id: "v1" }]);
    expect(peek(key)).toBeDefined();
    await createEditor(door, UPDATE, FIELDS).commit("v1", "title", "A", "Old");
    expect(peek(key)).toBeUndefined();
  });
  it("keeps its value until fresh rows arrive, then settles", async () => {
    const door = fakeDoor(async () => ok);
    const ed = createEditor(door, UPDATE, FIELDS);
    await ed.commit("v1", "title", "A", "Old");
    expect(ed.getState().shown).toEqual({ v1: { title: "A" } });
    ed.settle();
    expect(ed.getState().shown).toEqual({});
  });
});

describe("the cells that edit", () => {
  it("opens only an editable cell", () => {
    const ed = createEditor(fakeDoor(async () => ok), UPDATE, FIELDS, new Set(["total"]));
    ed.open("v1", "notes");
    expect(ed.getState().editing).toBeNull();
    ed.open("v1", "total");
    expect(ed.getState().editing).toBeNull();
    ed.open("v1", "title");
    expect(ed.getState().editing).toEqual({ id: "v1", key: "title" });
    ed.cancel();
    expect(ed.getState().editing).toBeNull();
  });
  it("a computed field never edits", async () => {
    const computed = computedKeys([{ name: "t", kind: "formula", target: "total", expr: {} }, { kind: "range", field: "qty" }]);
    expect([...computed]).toEqual(["total"]);
    const total = FIELDS.find((f) => f.key === "total") as FieldSpec;
    expect(isInlineEditable(total, computed)).toBe(false);
    expect(isInlineEditable(total, new Set())).toBe(true);
    const door = fakeDoor(async () => ok);
    await createEditor(door, UPDATE, FIELDS, computed).commit("v1", "total", "5", 1);
    expect(door.run).not.toHaveBeenCalled();
  });
  it("a roll-up target is computed too", () => {
    expect([...computedKeys([{ kind: "rollup", target: "sum" }])]).toEqual(["sum"]);
    expect(computedKeys(null).size).toBe(0);
  });
  it("only the eight scalar types edit, and a field with many values does not", () => {
    const types = ["text", "number", "select", "checkbox", "date", "url", "email", "phone"] as const;
    for (const type of types) expect(isInlineEditable({ key: "k", type }, new Set())).toBe(true);
    for (const type of ["longText", "money", "datetime", "multiSelect", "person", "relation", "file", "geo"] as const) {
      expect(isInlineEditable({ key: "k", type }, new Set())).toBe(false);
    }
    expect(isInlineEditable({ key: "k", type: "text", many: true }, new Set())).toBe(false);
  });
});
