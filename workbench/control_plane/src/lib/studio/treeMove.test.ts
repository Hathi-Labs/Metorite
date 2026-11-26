/**
 * STU-D18-2b fence: the move of a tree node (`treeMove.ts`).
 *
 * The store runs against a fake door. Each case names the rule that it holds.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll } from "@/lib/dataCache";
import type { ActionResult, StudioDataDoor } from "@/lib/studio/dataDoor";
import type { TreeNodeIn } from "@/lib/studio/treeModel";
import { createTreeMover, movedLine, placedNodes, TOP_OVER } from "@/lib/studio/treeMove";

beforeEach(() => clearAll());

type Run = (action: string, args?: Record<string, unknown>) => Promise<ActionResult>;
function fakeDoor(run: Run): StudioDataDoor & { run: ReturnType<typeof vi.fn> } {
  return { scope: "t:live", read: async () => [], run: vi.fn(run) };
}
function pendingDoor() {
  let settle: (r: ActionResult) => void = () => {};
  const door = fakeDoor(() => new Promise<ActionResult>((r) => { settle = r; }));
  return { door, settle: (r: ActionResult) => settle(r) };
}
const ok: ActionResult = { ok: true };

// a
// ├─ b
// │  └─ c
// └─ d
// e
const nodes: TreeNodeIn[] = [
  { id: "a", parentId: null, label: "A" },
  { id: "b", parentId: "a", label: "B" },
  { id: "c", parentId: "b", label: "C" },
  { id: "d", parentId: "a", label: "D" },
  { id: "e", parentId: null, label: "E" },
];
const label = (id: string) => nodes.find((n) => n.id === id)?.label ?? id;

describe("a move", () => {
  it("makes one update call with the id and the parent as {id}", async () => {
    const door = fakeDoor(async () => ok);
    const mover = createTreeMover(door, "object:part.update", "parent");
    await mover.move(nodes, "d", "e", label);
    expect(door.run).toHaveBeenCalledTimes(1);
    expect(door.run).toHaveBeenCalledWith("object:part.update", { id: "d", data: { parent: { id: "e" } } });
    expect(mover.getState().live).toBe("Moved D under E.");
    expect(mover.getState().alert).toBeNull();
  });

  it("sends null for the top level", async () => {
    const door = fakeDoor(async () => ok);
    const mover = createTreeMover(door, "u", "parent");
    await mover.move(nodes, "b", null, label);
    expect(door.run).toHaveBeenCalledWith("u", { id: "b", data: { parent: null } });
    expect(mover.getState().live).toBe(movedLine("B", null));
  });

  it("places the node at once, before the door answers", () => {
    const { door } = pendingDoor();
    const mover = createTreeMover(door, "u", "parent");
    void mover.move(nodes, "d", "e", label);
    expect(mover.getState().placed).toEqual({ d: "e" });
    expect(mover.getState().pending.has("d")).toBe(true);
    const shown = placedNodes(nodes, mover.getState().placed);
    expect(shown.find((n) => n.id === "d")?.parentId).toBe("e");
    // The input is not changed.
    expect(nodes.find((n) => n.id === "d")?.parentId).toBe("a");
  });

  it("a pending node cannot move twice", async () => {
    const { door, settle } = pendingDoor();
    const mover = createTreeMover(door, "u", "parent");
    void mover.move(nodes, "d", "e", label);
    await mover.move(nodes, "d", "b", label);
    expect(door.run).toHaveBeenCalledTimes(1);
    expect(mover.dragStart("d")).toBe(false);
    settle(ok);
  });
});

describe("a refusal", () => {
  it("of the model makes no call and says the plain sentence", async () => {
    const door = fakeDoor(async () => ok);
    const mover = createTreeMover(door, "u", "parent");
    await mover.move(nodes, "a", "c", label);
    expect(door.run).not.toHaveBeenCalled();
    expect(mover.getState().alert).toBe("A node cannot move under one of its own children.");
    expect(mover.getState().placed).toEqual({});
    await mover.move(nodes, "a", "a", label);
    expect(mover.getState().alert).toBe("A node cannot move under itself.");
  });

  it("of the door puts the node back and shows the message of the door", async () => {
    const door = fakeDoor(async () => ({ ok: false, message: "You can read this app. Only its owner can change it." }));
    const mover = createTreeMover(door, "u", "parent");
    await mover.move(nodes, "d", "e", label);
    const st = mover.getState();
    expect(st.placed).toEqual({});
    expect(st.pending.size).toBe(0);
    expect(st.alert).toBe("You can read this app. Only its owner can change it.");
    expect(placedNodes(nodes, st.placed)).toBe(nodes);
  });

  it("of a thrown call puts the node back with the plain failure sentence", async () => {
    const door = fakeDoor(async () => { throw new Error("raw network text"); });
    const mover = createTreeMover(door, "u", "parent");
    await mover.move(nodes, "d", "e", label);
    expect(mover.getState().placed).toEqual({});
    expect(mover.getState().alert).not.toContain("raw network text");
    expect(mover.getState().alert).toBeTruthy();
  });

  it("returns to the last accepted parent, and settle drops it", async () => {
    let n = 0;
    const door = fakeDoor(async () => (++n === 1 ? ok : { ok: false, message: "No." }));
    const mover = createTreeMover(door, "u", "parent");
    await mover.move(nodes, "d", "e", label);
    await mover.move(placedNodes(nodes, mover.getState().placed), "d", "b", label);
    expect(mover.getState().placed).toEqual({ d: "e" });
    mover.settle();
    expect(mover.getState().placed).toEqual({});
  });

  it("a new move clears the alert", async () => {
    let n = 0;
    const door = fakeDoor(async () => (++n === 1 ? { ok: false, message: "No." } : ok));
    const mover = createTreeMover(door, "u", "parent");
    await mover.move(nodes, "d", "e", label);
    expect(mover.getState().alert).toBe("No.");
    await mover.move(nodes, "d", "e", label);
    expect(mover.getState().alert).toBeNull();
  });
});

describe("the drag", () => {
  it("tracks the dragged node and the row under it", () => {
    const mover = createTreeMover(fakeDoor(async () => ok), "u", "parent");
    expect(mover.dragOver("e")).toBe(false);
    expect(mover.dragStart("d")).toBe(true);
    expect(mover.dragOver("e")).toBe(true);
    expect(mover.getState().over).toBe("e");
    mover.dragOver(TOP_OVER);
    expect(mover.getState().over).toBe(TOP_OVER);
    mover.dragLeave(false);
    expect(mover.getState().over).toBe(TOP_OVER);
    mover.dragLeave(true);
    expect(mover.getState().over).toBeNull();
    expect(mover.drop()).toBe("d");
    expect(mover.getState().dragId).toBeNull();
  });
});
