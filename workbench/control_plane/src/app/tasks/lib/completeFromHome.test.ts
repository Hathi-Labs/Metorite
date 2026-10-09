/**
 * My Day completes a task through the ONE store gesture (NS-3, D-PM-38).
 *
 * The claims, each against a fake store with the real store's shape:
 *
 *   1. The act calls `quickDispose(id, "DONE")`, the entry point My Tasks,
 *      the Calendar and Focus Mode use. It never writes by itself.
 *   2. A store that is not live is hydrated first, once for many clicks.
 *   3. A hydrate that fails, or a store that lacks the task, throws, so the
 *      card can put its row back. A demo store must never take the act.
 *   4. A parent with open subtasks: the store raises the question, and the
 *      act says "asked".
 *   5. A write failure that the store reports reaches the card, once.
 *
 * And the source half: the two cards that complete a task call this file,
 * and neither one writes a completion of its own.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

// The real store pulls the whole lens client. The fake below stands in.
vi.mock("./taskStore", () => ({ useTaskStore: {} }));

import { type CompletionStore, completeFromHome, onNextSyncFailure } from "./completeFromHome";

interface FakeState {
  backend: "live" | "demo";
  items: { id: string; subtaskCount?: number; subtaskDone?: number }[];
  subtaskPrompt: { ids: string[] } | null;
  syncFailure: { message: string; at: number } | null;
  hydrate: () => Promise<void>;
  quickDispose: (id: string, disposition: string) => void;
}

function fakeStore(init: Partial<FakeState> & { hydrateTo?: Partial<FakeState> }) {
  const listeners = new Set<(s: FakeState) => void>();
  const disposed: Array<[string, string]> = [];
  let hydrations = 0;
  const state: FakeState = {
    backend: "demo",
    items: [],
    subtaskPrompt: null,
    syncFailure: null,
    hydrate: async () => {
      hydrations += 1;
      await Promise.resolve();
      Object.assign(state, init.hydrateTo ?? {});
    },
    quickDispose: (id, disposition) => {
      disposed.push([id, disposition]);
      const row = state.items.find((i) => i.id === id);
      // The real store's D-PM-38 rule: a parent with open steps asks.
      if (row && (row.subtaskCount ?? 0) > (row.subtaskDone ?? 0)) {
        state.subtaskPrompt = { ids: [id] };
      }
    },
    ...init,
  };
  const store = {
    getState: () => state,
    subscribe: (fn: (s: FakeState) => void) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  } as unknown as CompletionStore;
  const emit = (patch: Partial<FakeState>) => {
    Object.assign(state, patch);
    for (const fn of listeners) fn(state);
  };
  return { store, disposed, emit, hydrations: () => hydrations, listeners };
}

describe("completeFromHome", () => {
  it("calls the store's own Mark done, quickDispose(id, DONE)", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "t1" }] });
    expect(await completeFromHome("t1", f.store)).toBe("completing");
    expect(f.disposed).toEqual([["t1", "DONE"]]);
    expect(f.hydrations()).toBe(0);
  });

  it("hydrates a store that is not live first, once for two quick clicks", async () => {
    const f = fakeStore({ hydrateTo: { backend: "live", items: [{ id: "a" }, { id: "b" }] } });
    await Promise.all([completeFromHome("a", f.store), completeFromHome("b", f.store)]);
    expect(f.hydrations()).toBe(1);
    expect(f.disposed).toEqual([
      ["a", "DONE"],
      ["b", "DONE"],
    ]);
  });

  it("refuses a demo store: a hydrate that failed must not swallow the act", async () => {
    const f = fakeStore({ hydrateTo: { backend: "demo", items: [{ id: "t1" }] } });
    await expect(completeFromHome("t1", f.store)).rejects.toThrow(/could not load/);
    expect(f.disposed).toEqual([]);
  });

  it("refuses a task the store does not hold", async () => {
    const f = fakeStore({ backend: "live", items: [] });
    await expect(completeFromHome("t9", f.store)).rejects.toThrow(/Open it in My Tasks/);
    expect(f.disposed).toEqual([]);
  });

  it("says 'asked' when the store raises the subtask question (D-PM-38)", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "p", subtaskCount: 3, subtaskDone: 1 }] });
    expect(await completeFromHome("p", f.store)).toBe("asked");
    expect(f.disposed).toEqual([["p", "DONE"]]);
  });
});

describe("onNextSyncFailure", () => {
  it("reports a NEW failure once, and then stops listening", () => {
    const f = fakeStore({ backend: "live", syncFailure: { message: "old", at: 1 } });
    const seen: string[] = [];
    onNextSyncFailure((m) => seen.push(m), f.store);
    f.emit({ items: [] });
    expect(seen).toEqual([]);
    f.emit({ syncFailure: { message: "Couldn't file the item.", at: 2 } });
    f.emit({ syncFailure: { message: "later", at: 3 } });
    expect(seen).toEqual(["Couldn't file the item."]);
    expect(f.listeners.size).toBe(0);
  });

  it("stops when asked, with no report", () => {
    const f = fakeStore({ backend: "live" });
    const seen: string[] = [];
    const stop = onNextSyncFailure((m) => seen.push(m), f.store);
    stop();
    f.emit({ syncFailure: { message: "x", at: 2 } });
    expect(seen).toEqual([]);
  });
});

describe("the cards complete through this file, and only through it", () => {
  const code = (rel: string) =>
    readFileSync(new URL(rel, import.meta.url), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(?<![:"'/])\/\/[^\n]*/g, "");

  it("Next actions calls completeFromHome, and no lens write", () => {
    const src = code("../components/NextActionsCard.tsx");
    expect(src).toMatch(/completeFromHome\(task\.id\)/);
    expect(src).not.toMatch(/lensCompleteItem|lensPatchItem|lensSetStatusId|\/complete[`"?]/);
  });

  it("Needs you's done act calls completeFromHome", () => {
    const src = code("../../../lib/shell/needs.ts");
    expect(src).toMatch(/completeFromHome\(item\.act_ref\)/);
  });

  it("My Day mounts the store's UndoToast, and AppShell hosts the subtask question", () => {
    expect(code("../../../lib/shell/MyDayPage.tsx")).toMatch(/<UndoToast \/>/);
    expect(code("../../../components/AppShell.tsx")).toMatch(/<SubtaskPromptHost \/>/);
  });
});
