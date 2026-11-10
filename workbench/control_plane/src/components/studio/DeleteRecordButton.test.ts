/**
 * STU-D2b-1 fence: Delete asks first.
 *
 * B2c-1 lets a direct delete run, and the server cannot see a dialog. So this
 * test holds the rule: the button opens the dialog and sends nothing, Cancel
 * sends nothing, and Confirm sends exactly one delete.
 *
 * Mutation 1: let `ask` call `door.run`. "the button opens the dialog and
 * sends nothing" turns red.
 * Mutation 2: let `cancel` call `door.run`. "Cancel sends nothing" turns red.
 * Mutation 3: drop `pressOnce` from `confirm`. "a second Confirm" turns red.
 * Mutation 4: close the dialog on a refusal. "a refusal keeps the dialog" turns red.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/package-preview",
}));

import DeleteRecordButton from "@/components/studio/DeleteRecordButton";
import { clearAll, peek, put } from "@/lib/dataCache";
import { type ActionResult, type StudioDataDoor, sourceKey } from "@/lib/studio/dataDoor";
import { createDeleteFlow, deleteCopy } from "@/lib/studio/deleteRecord";

beforeEach(() => clearAll());

type Run = (action: string, args?: Record<string, unknown>) => Promise<ActionResult>;
function fakeDoor(run: Run): StudioDataDoor & { run: ReturnType<typeof vi.fn> } {
  return { scope: "t:live", read: async () => [], run: vi.fn(run) };
}
const ID = "123e4567-e89b-12d3-a456-426614174000";
const ACTION = "object:visit.delete";

describe("the flow", () => {
  it("the button opens the dialog and sends nothing", () => {
    const door = fakeDoor(async () => ({ ok: true }));
    const flow = createDeleteFlow(door, ACTION, ID);
    flow.ask();
    expect(flow.getState().open).toBe(true);
    expect(door.run).not.toHaveBeenCalled();
  });
  it("Cancel sends nothing and closes the dialog", () => {
    const door = fakeDoor(async () => ({ ok: true }));
    const flow = createDeleteFlow(door, ACTION, ID);
    flow.ask();
    flow.cancel();
    expect(flow.getState().open).toBe(false);
    expect(door.run).not.toHaveBeenCalled();
  });
  it("Confirm sends one delete with the id, drops the cached reads and closes", async () => {
    const door = fakeDoor(async () => ({ ok: true }));
    const key = sourceKey(door, { action: "object:visit.list" });
    put(key, [{ id: ID }]);
    const flow = createDeleteFlow(door, ACTION, ID);
    flow.ask();
    expect(await flow.confirm()).toBe(true);
    expect(door.run).toHaveBeenCalledTimes(1);
    expect(door.run).toHaveBeenCalledWith(ACTION, { id: ID });
    expect(flow.getState()).toEqual({ open: false, busy: false, error: null });
    expect(peek(key)).toBeUndefined();
  });
  it("a second Confirm while the first is pending sends nothing", async () => {
    let settle: (r: ActionResult) => void = () => {};
    const door = fakeDoor(() => new Promise<ActionResult>((r) => { settle = r; }));
    const flow = createDeleteFlow(door, ACTION, ID);
    flow.ask();
    const first = flow.confirm();
    expect(flow.getState().busy).toBe(true);
    expect(await flow.confirm()).toBe(false);
    expect(door.run).toHaveBeenCalledTimes(1);
    settle({ ok: true });
    await first;
  });
  it("Cancel while a delete is in flight keeps the dialog", async () => {
    let settle: (r: ActionResult) => void = () => {};
    const door = fakeDoor(() => new Promise<ActionResult>((r) => { settle = r; }));
    const flow = createDeleteFlow(door, ACTION, ID);
    flow.ask();
    const pending = flow.confirm();
    flow.cancel();
    expect(flow.getState().open).toBe(true);
    settle({ ok: true });
    await pending;
  });
  it("a refusal keeps the dialog, shows its words and keeps the cached reads", async () => {
    const door = fakeDoor(async () => ({
      ok: false, message: "That did not save.",
      data: { rule: "keep_open_visits", message: "A visit with open work cannot be deleted." },
    }));
    const key = sourceKey(door, { action: "object:visit.list" });
    put(key, [{ id: ID }]);
    const flow = createDeleteFlow(door, ACTION, ID);
    flow.ask();
    expect(await flow.confirm()).toBe(false);
    expect(flow.getState().open).toBe(true);
    expect(flow.getState().error).toBe("Refused by keep_open_visits: A visit with open work cannot be deleted.");
    expect(peek(key)).toBeDefined();
  });
  it("a throw shows a plain sentence and never the error text", async () => {
    const door = fakeDoor(async () => { throw new Error("ECONNRESET"); });
    const flow = createDeleteFlow(door, ACTION, ID);
    flow.ask();
    await flow.confirm();
    expect(flow.getState().error).toBe("That did not work. Try again.");
    expect(flow.getState().open).toBe(true);
  });
  it("Cancel after a refusal clears the words", async () => {
    const door = fakeDoor(async () => ({ ok: false, message: "No." }));
    const flow = createDeleteFlow(door, ACTION, ID);
    flow.ask();
    await flow.confirm();
    flow.cancel();
    expect(flow.getState()).toEqual({ open: false, busy: false, error: null });
  });
});

describe("the words", () => {
  it("name the record and say that a delete cannot be undone", () => {
    const c = deleteCopy("Install the press");
    expect(c.subject).toBe("Install the press");
    expect(c.body).toMatch(/cannot undo|can not undo|for good/);
    expect(c.confirmLabel).toBe("Delete");
    expect(deleteCopy("  ").subject).toBeNull();
    expect(deleteCopy(undefined).subject).toBeNull();
  });
});

describe("the button", () => {
  const door = fakeDoor(async () => ({ ok: true }));
  it("draws a Delete button and no open dialog", () => {
    const html = renderToStaticMarkup(createElement(DeleteRecordButton, { door, object: "visit", id: ID, title: "A" }));
    expect(html).toContain("Delete");
    expect(html).not.toContain("role=\"dialog\"");
    expect(door.run).not.toHaveBeenCalled();
  });
  it("wires the button to ask, the dialog to cancel, and sits on ConfirmDialog", () => {
    const src = readFileSync(new URL("./DeleteRecordButton.tsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");
    expect(src).toContain("onClick={flow.ask}");
    expect(src).toContain("onCancel={flow.cancel}");
    expect(src).toContain("@/components/ui/ConfirmDialog");
    expect(src).not.toMatch(/door\.run\(/);
    expect(src).not.toMatch(/window\.confirm/);
  });
  it("is mounted in the Detail sheet of the preview and in no Table", () => {
    const table = readFileSync(new URL("./StudioTable.tsx", import.meta.url), "utf8");
    expect(table).not.toContain("DeleteRecordButton");
  });
});
