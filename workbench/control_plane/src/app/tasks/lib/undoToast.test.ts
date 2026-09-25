/**
 * My Tasks' undo through THE toast: the purge runs exactly once
 * (continuity P3, item 3). `undoToast.ts`'s header has the table.
 *
 * The fake toast closes the way Base UI's store does: `close` fires
 * `onClose` synchronously, an action click closes FIRST and runs the action
 * second, and a `show` with a key already up replaces it in place without
 * closing it.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { UNDO_WINDOW_SECONDS } from "./removal";
import {
  UNDO_TOAST_KEY,
  type UndoSnapshotLike,
  type UndoToastApi,
  syncUndoToast,
} from "./undoToast";

type Spec = Parameters<UndoToastApi["show"]>[0];

interface Snap extends UndoSnapshotLike {
  soft?: boolean;
}

function harness(opts: { productionDefer?: boolean; keys?: boolean } = {}) {
  const up = new Map<string, Spec>();
  const queue: (() => void)[] = [];
  const log = { purges: [] as string[], undone: 0, opened: [] as string[] };
  let snapshot: Snap | null = null;

  const close = (key: string) => {
    const spec = up.get(key);
    if (!spec) return;
    up.delete(key);
    spec.onClose();
  };
  const toast: UndoToastApi = {
    show: (spec) => up.set(spec.key, spec),
    dismiss: close,
  };
  const store = {
    current: () => snapshot,
    // `undoLastChange`: restores, clears the snapshot, never purges.
    undo: () => {
      snapshot = null;
      log.undone += 1;
    },
    // `dismissUndo`: clears first, THEN purges. A second call finds nothing.
    dismiss: () => {
      const s = snapshot;
      snapshot = null;
      if (s?.soft) log.purges.push(s.label);
    },
    openTask: (id: string) => log.opened.push(id),
  };
  // `productionDefer` passes NO defer, so the shipped default runs.
  const sync = () =>
    syncUndoToast(snapshot, toast, store, {
      keys: opts.keys,
      ...(opts.productionDefer ? {} : { defer: (fn: () => void) => queue.push(fn) }),
    });
  const flush = () => {
    while (queue.length) queue.shift()!();
  };

  return {
    log,
    up,
    flush,
    /** A change lands. Like the store, a pending purge is flushed first. */
    change(next: Snap) {
      if (snapshot?.soft) log.purges.push(snapshot.label);
      snapshot = next;
      sync();
      flush();
    },
    /** The window runs out. */
    timeout() {
      close(UNDO_TOAST_KEY);
      flush();
    },
    /** The × on the toast. */
    dismissButton() {
      close(UNDO_TOAST_KEY);
      flush();
    },
    /** The toast's action, as the substrate runs it: close, then act. */
    clickAction() {
      const spec = up.get(UNDO_TOAST_KEY)!;
      close(UNDO_TOAST_KEY);
      spec.action!.onClick();
      sync();
      flush();
    },
    /** The `u` key or ⌘Z: the store undoes, and the toast follows. */
    undoKey() {
      store.undo();
      sync();
      flush();
    },
    get snapshot() {
      return snapshot;
    },
  };
}

const DELETE = (label = "Deleted 1 task"): Snap => ({ label, soft: true });

describe("the purge runs exactly once", () => {
  it("when the window times out", () => {
    const h = harness();
    h.change(DELETE());
    h.timeout();
    expect(h.log.purges).toEqual(["Deleted 1 task"]);
    expect(h.snapshot).toBeNull();
  });

  it("when the reader dismisses the toast", () => {
    const h = harness();
    h.change(DELETE());
    h.dismissButton();
    expect(h.log.purges).toEqual(["Deleted 1 task"]);
  });

  it("NOT when the reader presses Undo, though the toast closes first", () => {
    const h = harness();
    h.change(DELETE());
    h.clickAction();
    expect(h.log.undone).toBe(1);
    expect(h.log.purges).toEqual([]);
    expect(h.up.size).toBe(0);
  });

  it("NOT when the u key undoes it", () => {
    const h = harness();
    h.change(DELETE());
    h.undoKey();
    expect(h.log.purges).toEqual([]);
    expect(h.up.size).toBe(0);
  });

  it("when a newer change replaces it: the old purge once, the new one later", () => {
    const h = harness();
    h.change(DELETE("first"));
    h.change(DELETE("second"));
    // The store flushed "first". The toast was replaced in place, not closed.
    expect(h.log.purges).toEqual(["first"]);
    expect(h.up.get(UNDO_TOAST_KEY)?.title).toBe("second");
    h.timeout();
    expect(h.log.purges).toEqual(["first", "second"]);
  });

  it("a late close of an old toast never finalizes the newer snapshot", () => {
    const h = harness();
    h.change(DELETE("first"));
    const stale = h.up.get(UNDO_TOAST_KEY)!;
    h.change(DELETE("second"));
    // The old toast's callback, arriving late (it was already ending).
    stale.onClose();
    h.flush();
    // The newer delete is still undoable: nothing finalized it early.
    expect(h.log.purges).toEqual(["first"]);
    expect(h.snapshot?.label).toBe("second");
    h.timeout();
    expect(h.log.purges).toEqual(["first", "second"]);
  });
});

describe("the shipped default defer (no queue injected)", () => {
  // Every test above injects a queue. This one runs the production default.
  // If that default became synchronous, `onClose` would check the snapshot
  // BEFORE the Undo action ran, find it still there, and purge the delete.
  it("an Undo click does not purge", async () => {
    const h = harness({ productionDefer: true });
    h.change(DELETE());
    h.clickAction();
    await Promise.resolve();
    await Promise.resolve();
    expect(h.log.undone).toBe(1);
    expect(h.log.purges).toEqual([]);
  });

  it("a timeout still purges, once, after the microtask", async () => {
    const h = harness({ productionDefer: true });
    h.change(DELETE());
    h.timeout();
    expect(h.log.purges).toEqual([]);
    await Promise.resolve();
    expect(h.log.purges).toEqual(["Deleted 1 task"]);
  });
});

describe("the key hint is said only where the keys work", () => {
  it("with keys bound, the toast names U and Ctrl+Z", () => {
    const h = harness();
    h.change(DELETE());
    expect(h.up.get(UNDO_TOAST_KEY)?.description).toMatch(/Press U or Ctrl\+Z/);
  });

  it("on a phone, or after the page unmounts, it names only the button", () => {
    const h = harness({ keys: false });
    h.change(DELETE());
    expect(h.up.get(UNDO_TOAST_KEY)?.description).toBeUndefined();
    expect(h.up.get(UNDO_TOAST_KEY)?.action?.label).toBe("Undo");
  });

  it("UndoToast passes keys: !isMobile, and drops the hint on unmount", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/UndoToast.tsx", import.meta.url)),
      "utf8",
    );
    expect(src).toMatch(/\{ keys: !isMobile \}/);
    const cleanup = src.slice(src.indexOf("return () => {"));
    expect(cleanup).toMatch(/^return \(\) => \{\s*syncUndoToast\([\s\S]*?\{ keys: false \}\);/);
  });
});

describe("what the toast says", () => {
  it("an Undo action, on the undo window the delete dialog quotes", () => {
    const h = harness();
    h.change(DELETE());
    const spec = h.up.get(UNDO_TOAST_KEY)!;
    expect(spec.action?.label).toBe("Undo");
    expect(spec.timeout).toBe(UNDO_WINDOW_SECONDS * 1000);
    expect(spec.variant).toBe("success");
  });

  it("a shared change offers the task, and finalizes once", () => {
    const h = harness();
    h.change({ label: "Moved", sharedChangeTaskId: "t9" });
    const spec = h.up.get(UNDO_TOAST_KEY)!;
    expect(spec.action?.label).toBe("Open task");
    h.clickAction();
    expect(h.log.opened).toEqual(["t9"]);
    expect(h.log.undone).toBe(0);
    expect(h.snapshot).toBeNull();
  });

  it("the app-local pill is gone: UndoToast draws through useToast", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/UndoToast.tsx", import.meta.url)),
      "utf8",
    );
    expect(src).toMatch(/useToast\(\)/);
    expect(src).toMatch(/syncUndoToast\(/);
    expect(src).not.toMatch(/fixed bottom|rounded-full|setTimeout/);
  });
});
