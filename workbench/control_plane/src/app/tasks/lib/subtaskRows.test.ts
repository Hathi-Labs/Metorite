/**
 * D-PM-38 (Subtasks S4) — My Tasks shows each subtask ONCE, with its parent
 * named (§12.9: "My Tasks is always Separate and has no setting").
 *
 * - A subtask whose parent is in the same list draws under it, once (B2).
 * - A subtask whose parent is not draws at the top level with its crumb.
 * - A parent's expander lists only the steps NOT already on the list.
 * - "File as subtask" keeps the task on the list, the same before a reload
 *   and after it (B8).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    apiFileUnder: vi.fn(),
    fetchItems: vi.fn(),
    fetchProjects: vi.fn(),
    fetchPeople: vi.fn(),
    fetchMyRoot: vi.fn(),
    fetchTaskSettings: vi.fn(),
  };
});
vi.mock("./lens", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lens")>();
  return { ...actual, lensGetItem: vi.fn() };
});

import {
  apiFileUnder,
  fetchItems,
  fetchMyRoot,
  fetchPeople,
  fetchProjects,
  fetchTaskSettings,
} from "./api";
import { lensGetItem } from "./lens";
import {
  myTaskRows,
  otherSteps,
  parentFactOf,
  stepsNotShown,
} from "./subtaskRows";
import { useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

const task = (id: string, over: Partial<MyTask> = {}): MyTask => ({
  id,
  source: "LOCAL",
  title: `Task ${id}`,
  disposition: "NEXT",
  isMine: true,
  createdAt: "2026-09-20T09:00:00Z",
  updatedAt: "2026-09-20T09:00:00Z",
  ...over,
});

const layout = (items: MyTask[]) =>
  myTaskRows(items).map((r) => `${"  ".repeat(r.depth)}${r.item.id}${r.crumb ? " ↳" : ""}`);

describe("myTaskRows — each subtask once", () => {
  it("draws a step ONCE, under its parent, when the parent is on the list", () => {
    const parent = task("p", { subtaskCount: 1 });
    const step = task("s", { parentItemId: "p", parent: { id: "p", title: "Task p" } });
    const rows = myTaskRows([step, parent]);
    expect(rows.map((r) => r.item.id)).toEqual(["p", "s"]);
    expect(rows.filter((r) => r.item.id === "s")).toHaveLength(1);
    expect(rows[1]).toMatchObject({ depth: 1, crumb: false });
  });

  it("draws a step at the top level WITH its crumb when the parent is not here", () => {
    const step = task("s", { parentItemId: "p", parent: { id: "p", title: "Launch" } });
    expect(myTaskRows([task("a"), step])).toEqual([
      { item: task("a"), depth: 0, crumb: false },
      { item: step, depth: 0, crumb: true },
    ]);
  });

  it("nests at any depth, and keeps the incoming order within a level", () => {
    const items = [
      task("c", { parentItemId: "b" }),
      task("x"),
      task("b", { parentItemId: "a" }),
      task("a"),
      task("d", { parentItemId: "a" }),
    ];
    expect(layout(items)).toEqual(["x", "a", "  b", "    c", "  d"]);
  });

  it("a top-level task never carries a crumb", () => {
    expect(myTaskRows([task("a")])[0].crumb).toBe(false);
  });
});

describe("the parent's expander — only the steps not on the list", () => {
  const parent = task("p", { subtaskCount: 3 });
  const mine = task("s1", { parentItemId: "p" });
  const list = [parent, mine];

  it("counts the steps that are NOT drawn", () => {
    // Three visible children, one of them on my list: two for the expander.
    expect(stepsNotShown(parent, list)).toBe(2);
    expect(stepsNotShown(task("q", { subtaskCount: 1 }), [task("q"), task("s", { parentItemId: "q" })])).toBe(0);
    expect(stepsNotShown(task("lone"), [task("lone")])).toBe(0);
  });

  it("lists only the children not shown elsewhere", () => {
    const loaded = [mine, task("s2", { isMine: false }), task("s3", { isMine: false })];
    const shown = new Set(list.map((i) => i.id));
    expect(otherSteps(loaded, shown).map((c) => c.id)).toEqual(["s2", "s3"]);
  });
});

describe("File as subtask survives a store rehydrate (B8)", () => {
  const parent = task("p", { title: "Launch", taskNumber: 7 });
  const capture = task("c", { disposition: "INBOX" });
  // What the gateway serves for the filed task after the move.
  const served = task("c", {
    disposition: "INBOX",
    parentItemId: "p",
    parent: { id: "p", ref: "#7", title: "Launch", archived: false },
  });

  beforeEach(() => {
    useTaskStore.setState({ items: [parent, capture], backend: "live", undoSnapshot: null });
    vi.mocked(apiFileUnder).mockResolvedValue({ ...parent, subtaskCount: 1 });
    vi.mocked(lensGetItem).mockResolvedValue(served);
    vi.mocked(fetchItems).mockResolvedValue([{ ...parent, subtaskCount: 1 }, served]);
    vi.mocked(fetchProjects).mockResolvedValue([]);
    vi.mocked(fetchPeople).mockResolvedValue([]);
    vi.mocked(fetchMyRoot).mockResolvedValue(null);
    vi.mocked(fetchTaskSettings).mockResolvedValue(useTaskStore.getState().settings);
  });
  afterEach(() => vi.clearAllMocks());

  it("keeps the task, marks it, and draws it the same before and after a reload", async () => {
    // The optimistic state, before the server answers.
    const pending = useTaskStore.getState().fileUnderParent("c", "p");
    const before = useTaskStore.getState().items;
    expect(before.map((i) => i.id)).toContain("c");
    expect(before.find((i) => i.id === "c")).toMatchObject({
      parentItemId: "p",
      parent: parentFactOf(parent),
    });
    const optimistic = layout(before);
    await pending;
    const settled = layout(useTaskStore.getState().items);

    // The reload: the store takes what the gateway serves.
    await useTaskStore.getState().hydrate();
    const reloaded = layout(useTaskStore.getState().items);

    expect(optimistic).toEqual(["p", "  c"]);
    expect(settled).toEqual(optimistic);
    expect(reloaded).toEqual(optimistic);
    // In a list without the parent (the Inbox), the same task carries its crumb.
    const inbox = useTaskStore.getState().items.filter((i) => i.disposition === "INBOX");
    expect(layout(inbox)).toEqual(["c ↳"]);
  });

  it("builds the crumb the gateway builds", () => {
    expect(parentFactOf(parent)).toEqual(served.parent);
  });

  it("offers no undo that would copy the task", async () => {
    await useTaskStore.getState().fileUnderParent("c", "p");
    expect(useTaskStore.getState().undoSnapshot).toBeNull();
  });
});
