/**
 * A Home card's row through a whole act (NS-3 review, 2026-10-10).
 *
 * The card's state is two setters: the set of rows it hides, and the error
 * of each row. This drives the REAL path a card runs: `runAct` →
 * `markDoneFromHome` → the store's gesture, with `rowMover` as the row. Only
 * the store is a fake, shaped like the real one.
 *
 * The case the review found: a Done that only ASKS the subtask question hid
 * the row, and Cancel never brought it back. The summary then counted one
 * thing fewer than there was.
 *
 * It also holds the focus rule: when a row leaves, focus goes to the next
 * row's act, or to the card's heading after the last row.
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/app/tasks/lib/taskStore", () => ({ useTaskStore: {} }));

import type { CompletionStore } from "@/app/tasks/lib/completeFromHome";

import { focusTarget, keepRemoved, rowMover } from "./HomeCard";
import { type NeedsItem, runAct } from "./needs";

interface State {
  backend: "live";
  items: { id: string; subtaskCount?: number; disposition?: string }[];
  subtaskPrompt: { ids: string[] } | null;
  syncFailure: null;
  undoSnapshot: { changedIds: string[]; items: State["items"] } | null;
  refreshItem: (id: string) => Promise<void>;
  hydrate: () => Promise<void>;
  quickDispose: (id: string, d: string, opts?: { includeSubtasks?: boolean }) => void;
}

function store(items: State["items"]) {
  const listeners = new Set<(s: State) => void>();
  const emit = (patch: Partial<State>) => {
    Object.assign(state, patch);
    for (const fn of [...listeners]) fn(state);
  };
  const state: State = {
    backend: "live",
    items,
    subtaskPrompt: null,
    syncFailure: null,
    undoSnapshot: null,
    refreshItem: async () => {},
    hydrate: async () => {},
    quickDispose: (id, _d, opts) => {
      const row = state.items.find((i) => i.id === id);
      if (opts?.includeSubtasks === undefined && (row?.subtaskCount ?? 0) > 0) {
        emit({ subtaskPrompt: { ids: [id] } });
        return;
      }
      emit({
        undoSnapshot: { changedIds: [id], items: state.items },
        items: state.items.map((i) => (i.id === id ? { ...i, disposition: "DONE" } : i)),
      });
    },
  };
  const s = {
    getState: () => state,
    subscribe: (fn: (st: State) => void) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  } as unknown as CompletionStore;
  return {
    s,
    cancel: () => emit({ subtaskPrompt: null }),
    // The store's Undo: the rows and the snapshot go back in ONE set.
    undo: () => emit({ items: state.undoSnapshot!.items, undoSnapshot: null }),
    answer: (all: boolean) => {
      const id = state.subtaskPrompt!.ids[0];
      emit({ subtaskPrompt: null });
      state.quickDispose(id, "DONE", { includeSubtasks: all });
    },
  };
}

/** The card's two pieces of state, as the hooks hold them. */
function card() {
  let removed = new Set<string>();
  const errors: Record<string, string> = {};
  const remove = (id: string, gone: boolean) => (gone ? removed.add(id) : removed.delete(id));
  const setError = (id: string, m: string | null) => {
    if (m) errors[id] = m;
    else delete errors[id];
  };
  // The hooks' rule when a new feed answer lands (`keepRemoved`).
  const answer = (held: string[]) => {
    removed = new Set(keepRemoved(removed, held));
  };
  /** The rows the card draws for a feed answer. */
  const drawn = (held: string[]) => held.filter((id) => !removed.has(id));
  return { get removed() { return removed; }, errors, remove, setError, answer, drawn };
}

const row = (id: string): NeedsItem => ({
  id: `tasks:${id}`,
  app: "tasks",
  kind: "overdue",
  title: id,
  detail: null,
  href: `/projects?task=${id}`,
  at: null,
  act: "done",
  act_ref: id,
});

const tick = () => new Promise((r) => setTimeout(r, 0));

describe("a Needs you row through a Done", () => {
  it("CANCEL on the subtask question: the row is back, with no error", async () => {
    const st = store([{ id: "parent", subtaskCount: 3 }]);
    const c = card();
    const item = row("parent");
    const out = runAct(item, rowMover(item.id, c.remove, c.setError), st.s);
    await tick();
    expect(c.removed.has(item.id)).toBe(false); // back while the question is up
    st.cancel();
    await expect(out).resolves.toBe("kept");
    expect(c.removed.has(item.id)).toBe(false);
    expect(c.errors).toEqual({});
  });

  it("an ANSWER on the subtask question: the row leaves", async () => {
    const st = store([{ id: "parent", subtaskCount: 3 }]);
    const c = card();
    const item = row("parent");
    const out = runAct(item, rowMover(item.id, c.remove, c.setError), st.s);
    await tick();
    st.answer(false);
    await expect(out).resolves.toBe("done");
    expect(c.removed.has(item.id)).toBe(true);
  });

  it("a plain task leaves at once", async () => {
    const st = store([{ id: "t1" }]);
    const c = card();
    const item = row("t1");
    const out = runAct(item, rowMover(item.id, c.remove, c.setError), st.s);
    expect(c.removed.has(item.id)).toBe(true); // before any answer comes back
    await expect(out).resolves.toBe("done");
    expect(c.removed.has(item.id)).toBe(true);
  });
});

describe("Done, then Undo, while the feed reads again (review round 3)", () => {
  it("draws A again when the feed answers WITH A after the Undo", async () => {
    const st = store([{ id: "A" }, { id: "B" }]);
    const c = card();
    const a = row("A");
    await runAct(a, rowMover(a.id, c.remove, c.setError), st.s);
    expect(c.drawn(["tasks:A", "tasks:B"])).toEqual(["tasks:B"]);
    st.undo(); // the store's Undo, before the feed's re-read lands
    c.answer(["tasks:A", "tasks:B"]); // the cache's right answer, A held
    expect(c.drawn(["tasks:A", "tasks:B"])).toEqual(["tasks:A", "tasks:B"]);
  });

  it("keepRemoved forgets a hidden row the answer no longer holds, and keeps the rest", () => {
    const hidden = new Set(["x", "y"]);
    expect([...keepRemoved(hidden, ["y", "z"])]).toEqual(["y"]);
    expect(keepRemoved(hidden, ["x", "y"])).toBe(hidden); // nothing to change
  });
});

describe("focus when a row leaves", () => {
  const acts = ["a", "b", "c"];
  it("goes to the NEXT row's act", () => {
    expect(focusTarget(acts, "a", "heading")).toBe("b");
    expect(focusTarget(acts, "b", "heading")).toBe("c");
  });
  it("goes to the card's heading after the last row", () => {
    expect(focusTarget(acts, "c", "heading")).toBe("heading");
    expect(focusTarget(["only"], "only", "heading")).toBe("heading");
  });
  it("moves nothing for a control that is not a row's act", () => {
    expect(focusTarget(acts, "z", "heading")).toBeNull();
  });
  it("is wired: both cards mark their acts, and the frame's heading takes focus", async () => {
    const { readFileSync } = await import("node:fs");
    const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
    expect(read("./NeedsYouCard.tsx").match(/data-row-act=""/g)?.length).toBe(2);
    expect(read("../../app/tasks/components/NextActionsCard.tsx")).toMatch(/data-row-act=""/);
    const frame = read("./HomeCard.tsx");
    expect(frame).toMatch(/data-home-card=""/);
    expect(frame).toMatch(/<h2 id=\{headingId\} tabIndex=\{-1\}/);
    expect(frame).toMatch(/hide\(\) \{\s*moveFocusAfterLeave\(from\);/);
  });
});
