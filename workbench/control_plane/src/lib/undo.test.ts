/**
 * The undo/redo stack, and the keyboard that drives it.
 *
 * The claims worth pinning are the ones a plausible implementation gets wrong
 * while looking correct:
 *
 * * **a new action discards the future.** Keeping it lets redo jump you onto a
 *   timeline you branched away from.
 * * **a missing key inverts to `null`, not to absent.** The single most common
 *   hand-written-inverse bug: undoing "set a start date" leaves the date in
 *   place and reads as undo doing nothing.
 * * **Ctrl+Z inside a text field is the BROWSER's.** Otherwise typing a note
 *   and pressing undo reverts somebody's task instead of the sentence.
 * * **the stack is bounded.** Every entry closes over the values it restores.
 */

import { describe, expect, it } from "vitest";

import {
  EMPTY_UNDO,
  UNDO_DEPTH,
  type UndoEntry,
  canRedo,
  canUndo,
  inversePatch,
  record,
  redoLabel,
  stepBack,
  stepForward,
  undoLabel,
} from "./undo";
import { isRedoShortcut, isTypingTarget, isUndoShortcut } from "./keyboard";

const entry = (label: string): UndoEntry => ({
  label,
  undo: () => {},
  redo: () => {},
});

const key = (over: Partial<Parameters<typeof isUndoShortcut>[0]>) => ({
  key: "z",
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  ...over,
});

describe("the stack", () => {
  it("starts empty and offers nothing", () => {
    expect(canUndo(EMPTY_UNDO)).toBe(false);
    expect(canRedo(EMPTY_UNDO)).toBe(false);
    expect(undoLabel(EMPTY_UNDO)).toBeNull();
    expect(redoLabel(EMPTY_UNDO)).toBeNull();
  });

  it("undoes and redoes in LIFO order", () => {
    let stack = record(record(EMPTY_UNDO, entry("first")), entry("second"));
    expect(undoLabel(stack)).toBe("second");

    const back = stepBack(stack);
    expect(back?.entry.label).toBe("second");
    stack = back!.stack;
    expect(undoLabel(stack)).toBe("first");
    expect(redoLabel(stack)).toBe("second");

    const forward = stepForward(stack);
    expect(forward?.entry.label).toBe("second");
    expect(undoLabel(forward!.stack)).toBe("second");
    expect(canRedo(forward!.stack)).toBe(false);
  });

  it("DISCARDS the future when a new action is recorded", () => {
    // The branch rule. Without it, redo replays a step that describes a state
    // the new action has already moved away from.
    const two = record(record(EMPTY_UNDO, entry("a")), entry("b"));
    const undone = stepBack(two)!.stack;
    expect(canRedo(undone)).toBe(true);

    const branched = record(undone, entry("c"));
    expect(canRedo(branched)).toBe(false);
    expect(undoLabel(branched)).toBe("c");
  });

  it("returns null at either end rather than throwing", () => {
    // The provider calls these on every keypress; an empty stack is the normal
    // case, not an error.
    expect(stepBack(EMPTY_UNDO)).toBeNull();
    expect(stepForward(EMPTY_UNDO)).toBeNull();
  });

  it("is bounded, dropping the OLDEST", () => {
    let stack = EMPTY_UNDO;
    for (let i = 0; i < UNDO_DEPTH + 10; i += 1) {
      stack = record(stack, entry(`step ${i}`));
    }
    expect(stack.past).toHaveLength(UNDO_DEPTH);
    expect(undoLabel(stack)).toBe(`step ${UNDO_DEPTH + 9}`);
    // The first ten are gone, not the last ten.
    expect(stack.past[0]?.label).toBe("step 10");
  });

  it("never mutates the stack it was given", () => {
    // The provider keeps a previous stack to restore on failure. Mutation
    // would make that restore a no-op, silently losing the failed step.
    const before = record(EMPTY_UNDO, entry("a"));
    const snapshot = [...before.past];
    record(before, entry("b"));
    stepBack(before);
    expect(before.past).toEqual(snapshot);
  });
});

describe("inversePatch", () => {
  it("reverts only the keys the change touched", () => {
    // Sending the whole record back would revert fields the user never
    // altered, including any a colleague changed in between.
    const before = { id: "t1", title: "Ship it", due_at: "2026-08-20", importance: 2 };
    expect(inversePatch(before, { due_at: "2026-08-25" })).toEqual({
      due_at: "2026-08-20",
    });
  });

  it("inverts a key the record did NOT have to null", () => {
    // Setting a start date on a task with none must undo to *no start date*.
    // Omitting the key leaves the new value in place, and undo looks broken.
    const before = { id: "t1", due_at: "2026-08-20" };
    expect(inversePatch(before, { start_date: "2026-08-10" })).toEqual({
      start_date: null,
    });
  });

  it("keeps an explicit null as null rather than treating it as absent", () => {
    const before = { id: "t1", start_date: null };
    expect(inversePatch(before, { start_date: "2026-08-10" })).toEqual({
      start_date: null,
    });
  });

  it("round-trips a multi-field patch", () => {
    // The timeline's resize writes both ends at once; its inverse must restore
    // both, including the one that was null.
    const before = { start_date: null, due_at: "2026-08-20" };
    const patch = { start_date: "2026-08-20", due_at: "2026-08-25" };
    const back = inversePatch(before, patch);
    expect({ ...before, ...patch, ...back }).toEqual(before);
  });
});

describe("the shortcuts", () => {
  it("takes Ctrl+Z and Cmd+Z for undo", () => {
    expect(isUndoShortcut(key({ ctrlKey: true }))).toBe(true);
    expect(isUndoShortcut(key({ metaKey: true }))).toBe(true);
    expect(isUndoShortcut(key({}))).toBe(false);
  });

  it("does not read Ctrl+Shift+Z as undo", () => {
    // The overlap that makes redo undo one step too many.
    expect(isUndoShortcut(key({ ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(isRedoShortcut(key({ ctrlKey: true, shiftKey: true }))).toBe(true);
  });

  it("takes Ctrl+Y for redo too", () => {
    // Shift+Z is the Mac and web norm, Ctrl+Y the Windows one. A user whose
    // muscle memory is the other one experiences "redo is broken".
    expect(isRedoShortcut(key({ key: "y", ctrlKey: true }))).toBe(true);
    expect(isUndoShortcut(key({ key: "y", ctrlKey: true }))).toBe(false);
  });

  it("ignores the Alt variants, which belong to the OS", () => {
    expect(isUndoShortcut(key({ ctrlKey: true, altKey: true }))).toBe(false);
    expect(isRedoShortcut(key({ ctrlKey: true, shiftKey: true, altKey: true }))).toBe(
      false
    );
  });

  it("is case-insensitive, because Shift+Z reports an uppercase key", () => {
    // `event.key` is "Z" whenever shift is held — so a `=== "z"` comparison
    // makes redo unreachable, on the exact binding it is bound to.
    expect(isRedoShortcut(key({ key: "Z", ctrlKey: true, shiftKey: true }))).toBe(true);
  });
});

describe("isTypingTarget", () => {
  it("claims the fields a browser's own undo owns", () => {
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
      expect(isTypingTarget({ tagName })).toBe(true);
    }
    expect(isTypingTarget({ isContentEditable: true })).toBe(true);
  });

  it("counts <select>, which six of the seven hand-rolled copies missed", () => {
    // The divergence the shared predicate exists to end.
    expect(isTypingTarget({ tagName: "select" })).toBe(true);
  });

  it("leaves everything else to the app", () => {
    expect(isTypingTarget({ tagName: "DIV" })).toBe(false);
    expect(isTypingTarget({ tagName: "BUTTON" })).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
    expect(isTypingTarget(undefined)).toBe(false);
  });
});
