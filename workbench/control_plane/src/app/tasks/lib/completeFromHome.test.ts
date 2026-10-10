/**
 * My Day completes a task through the ONE store gesture (NS-3, D-PM-38).
 *
 * The claims, each against a fake store with the real store's shape:
 *
 *   1. The act calls `quickDispose(id, "DONE")`, the entry point My Tasks,
 *      the Calendar and Focus Mode use. It never writes by itself.
 *   2. A store that is not live is hydrated first, once for many clicks.
 *   3. A hydrate that fails throws, so the card can put its row back. A demo
 *      store must never take the act.
 *   4. The store's row may be older than My Day's. A held task is read again
 *      through `refreshItem` first, so a parent that gained open subtasks
 *      ASKS. A task the store never saw makes it hydrate again.
 *   5. After the store asks, the row stays. It leaves only if the answer
 *      completes the task. A question that closes with no done keeps it.
 *   6. A write failure reaches the card once, also after a late answer.
 *
 * And the source half: the two cards complete through this file, and
 * neither one writes a completion of its own.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

// The real store pulls the whole lens client. The fake below stands in.
vi.mock("./taskStore", () => ({ useTaskStore: {} }));

import {
  type CompletionStore,
  type DoneRow,
  completeFromHome,
  markDoneFromHome,
  onUndone,
  onNextSyncFailure,
  whenAnswered,
} from "./completeFromHome";

interface Row {
  id: string;
  subtaskCount?: number;
  subtaskDone?: number;
  disposition?: string;
}

interface FakeState {
  backend: "live" | "demo";
  items: Row[];
  subtaskPrompt: { ids: string[] } | null;
  syncFailure: { message: string; at: number } | null;
  undoSnapshot: { changedIds: string[]; items: Row[] } | null;
  hydrate: () => Promise<void>;
  refreshItem: (id: string) => Promise<void>;
  quickDispose: (id: string, disposition: string, opts?: { includeSubtasks?: boolean }) => void;
  answerSubtaskPrompt: (includeSubtasks: boolean) => void;
  cancelSubtaskPrompt: () => void;
  undoLastChange: () => void;
}

function fakeStore(
  init: Partial<FakeState> & { hydrateTo?: Partial<FakeState>; server?: Record<string, Row> },
) {
  const listeners = new Set<(s: FakeState) => void>();
  const disposed: Array<[string, string]> = [];
  const refreshed: string[] = [];
  let hydrations = 0;
  const emit = (patch: Partial<FakeState>) => {
    Object.assign(state, patch);
    for (const fn of [...listeners]) fn(state);
  };
  const state: FakeState = {
    backend: "demo",
    items: [],
    subtaskPrompt: null,
    syncFailure: null,
    undoSnapshot: null,
    hydrate: async () => {
      hydrations += 1;
      await Promise.resolve();
      emit(init.hydrateTo ?? {});
    },
    refreshItem: async (id) => {
      refreshed.push(id);
      await Promise.resolve();
      const row = init.server?.[id];
      if (row) emit({ items: state.items.map((i) => (i.id === id ? row : i)) });
    },
    // The real store's D-PM-38 rule: a parent with open steps asks first,
    // unless the answer is already given.
    quickDispose: (id, disposition, opts) => {
      const row = state.items.find((i) => i.id === id);
      if (opts?.includeSubtasks === undefined && row && (row.subtaskCount ?? 0) > (row.subtaskDone ?? 0)) {
        emit({ subtaskPrompt: { ids: [id] } });
        return;
      }
      disposed.push([id, disposition]);
      emit({
        undoSnapshot: { changedIds: [id], items: state.items },
        items: state.items.map((i) => (i.id === id ? { ...i, disposition: "DONE" } : i)),
      });
    },
    // As the real store: clear the question, THEN run the gesture.
    answerSubtaskPrompt: (includeSubtasks) => {
      const ids = state.subtaskPrompt?.ids ?? [];
      emit({ subtaskPrompt: null });
      state.quickDispose(ids[0], "DONE", { includeSubtasks });
    },
    cancelSubtaskPrompt: () => emit({ subtaskPrompt: null }),
    // As the real store: the rows and the snapshot go back in ONE set.
    undoLastChange: () => {
      const snap = state.undoSnapshot;
      if (snap) emit({ items: snap.items, undoSnapshot: null });
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
  return { store, state, disposed, refreshed, emit, hydrations: () => hydrations, listeners };
}

/** A row that records what the card would draw. */
function recorder() {
  const log: string[] = [];
  let hidden = false;
  let error: string | null = null;
  const row: DoneRow = {
    hide: () => {
      hidden = true;
      error = null;
      log.push("hide");
    },
    show: () => {
      hidden = false;
      log.push("show");
    },
    fail: (m) => {
      hidden = false;
      error = m;
      log.push("fail");
    },
    keep: () => {
      log.push("keep");
    },
  };
  return { row, log, hidden: () => hidden, error: () => error };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

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
    expect(f.disposed.map(([id]) => id).sort()).toEqual(["a", "b"]);
    // Just loaded: no second read of the row.
    expect(f.refreshed).toEqual([]);
  });

  it("refuses a demo store: a hydrate that failed must not swallow the act", async () => {
    const f = fakeStore({ hydrateTo: { backend: "demo", items: [{ id: "t1" }] } });
    await expect(completeFromHome("t1", f.store)).rejects.toThrow(/could not load/);
    expect(f.disposed).toEqual([]);
  });

  it("reads a held task again first, so a parent that gained open subtasks ASKS", async () => {
    // The store loaded "p" with no subtasks. Since then it gained three.
    const f = fakeStore({
      backend: "live",
      items: [{ id: "p" }],
      server: { p: { id: "p", subtaskCount: 3, subtaskDone: 0 } },
    });
    expect(await completeFromHome("p", f.store)).toBe("asked");
    expect(f.refreshed).toEqual(["p"]);
    expect(f.disposed).toEqual([]);
  });

  it("hydrates again for a task that reached My Day after the store loaded", async () => {
    const f = fakeStore({ backend: "live", items: [], hydrateTo: { items: [{ id: "new" }] } });
    expect(await completeFromHome("new", f.store)).toBe("completing");
    expect(f.hydrations()).toBe(1);
    expect(f.disposed).toEqual([["new", "DONE"]]);
  });

  it("refuses a task the store does not hold even after a fresh load", async () => {
    const f = fakeStore({ backend: "live", items: [] });
    await expect(completeFromHome("t9", f.store)).rejects.toThrow(/Open it in My Tasks/);
    expect(f.hydrations()).toBe(1);
    expect(f.disposed).toEqual([]);
  });
});

describe("whenAnswered", () => {
  it("is true when the answer completes the task", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "p", subtaskCount: 2 }] });
    await completeFromHome("p", f.store);
    const answered = whenAnswered("p", f.store);
    f.state.answerSubtaskPrompt(false);
    await expect(answered).resolves.toBe(true);
  });

  it("is false when the question closes with no done", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "p", subtaskCount: 2 }] });
    await completeFromHome("p", f.store);
    const answered = whenAnswered("p", f.store);
    f.state.cancelSubtaskPrompt();
    await expect(answered).resolves.toBe(false);
  });
});

describe("markDoneFromHome, the optimistic row", () => {
  it("a plain task: the row leaves and stays gone", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "t1" }] });
    const r = recorder();
    await expect(markDoneFromHome("t1", r.row, f.store)).resolves.toBe("done");
    // `keep` restarts the hold once the store has begun the write.
    expect(r.log).toEqual(["hide", "keep"]);
    expect(r.hidden()).toBe(true);
  });

  it("CANCEL: the store asks, the question closes with no done, and the row stays", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "p", subtaskCount: 3 }] });
    const r = recorder();
    const out = markDoneFromHome("p", r.row, f.store);
    await tick();
    // While the question is up, the row is back on the card.
    expect(r.hidden()).toBe(false);
    f.state.cancelSubtaskPrompt();
    await expect(out).resolves.toBe("kept");
    expect(r.log).toEqual(["hide", "show"]);
    expect(r.hidden()).toBe(false);
    expect(r.error()).toBeNull();
    expect(f.disposed).toEqual([]);
  });

  it("ANSWERED: the row leaves only when the answer completes the task", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "p", subtaskCount: 3 }] });
    const r = recorder();
    const out = markDoneFromHome("p", r.row, f.store);
    await tick();
    f.state.answerSubtaskPrompt(true);
    await expect(out).resolves.toBe("done");
    expect(r.log).toEqual(["hide", "show", "hide", "keep"]);
    expect(f.disposed).toEqual([["p", "DONE"]]);
  });

  it("a failure the store reports AFTER a late answer still brings the row back", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "p", subtaskCount: 3 }] });
    const r = recorder();
    const out = markDoneFromHome("p", r.row, f.store);
    await tick();
    f.state.answerSubtaskPrompt(false);
    await out;
    f.emit({ syncFailure: { message: "Couldn't file the item.", at: 2 } });
    expect(r.hidden()).toBe(false);
    expect(r.error()).toMatch(/Could not mark it done/);
  });

  it("a store that cannot load brings the row back with its reason", async () => {
    const f = fakeStore({ hydrateTo: { backend: "demo" } });
    const r = recorder();
    await expect(markDoneFromHome("t1", r.row, f.store)).resolves.toBe("failed");
    expect(r.log).toEqual(["hide", "fail"]);
    expect(r.error()).toMatch(/could not load/);
  });
});

describe("the store's Undo brings the row back", () => {
  it("shows the row again when Undo puts the task back from DONE", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "t1" }] });
    const r = recorder();
    await markDoneFromHome("t1", r.row, f.store);
    expect(r.hidden()).toBe(true);
    f.state.undoLastChange();
    expect(r.hidden()).toBe(false);
    expect(r.log).toEqual(["hide", "keep", "show"]);
  });

  it("watches nothing when the store holds no Undo for this task", () => {
    const f = fakeStore({ backend: "live", items: [{ id: "t1" }] });
    let back = 0;
    onUndone("t1", () => (back += 1), f.store);
    expect(f.listeners.size).toBe(0);
    expect(back).toBe(0);
  });

  it("stops watching when a newer change takes the Undo", async () => {
    const f = fakeStore({ backend: "live", items: [{ id: "t1" }, { id: "t2" }] });
    const r = recorder();
    await markDoneFromHome("t1", r.row, f.store);
    f.state.quickDispose("t2", "DONE"); // a newer change, its own Undo
    f.state.undoLastChange(); // undoes t2, not t1
    expect(r.hidden()).toBe(true);
    expect(f.listeners.size).toBe(1); // only the 30 s failure watch is left
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

  it("Next actions calls markDoneFromHome, and no lens write", () => {
    const src = code("../components/NextActionsCard.tsx");
    expect(src).toMatch(/markDoneFromHome\(task\.id, rowMover\(/);
    expect(src).not.toMatch(/lensCompleteItem|lensPatchItem|lensSetStatusId|\/complete[`"?]/);
  });

  it("Needs you's done act calls markDoneFromHome", () => {
    expect(code("../../../lib/shell/needs.ts")).toMatch(/markDoneFromHome\(item\.act_ref, row, store\)/);
    expect(code("../../../lib/shell/NeedsYouCard.tsx")).toMatch(/runAct\(item, rowMover\(/);
  });

  it("My Day mounts the store's UndoToast, and AppShell hosts the subtask question", () => {
    expect(code("../../../lib/shell/MyDayPage.tsx")).toMatch(/<UndoToast \/>/);
    expect(code("../../../components/AppShell.tsx")).toMatch(/<SubtaskPromptHost \/>/);
  });
});
