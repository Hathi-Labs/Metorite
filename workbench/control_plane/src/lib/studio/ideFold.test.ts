/**
 * STU-G3a fix 2 fence: the IDE folds the app nav only when the member allows the auto fold, marks that
 * it did, and gives the nav back on leave only when no other rule holds it folded.
 *
 * Mutation 1: ignore `enabled` in `foldOnOpen`. "folds only when the auto fold is on" turns red.
 * Mutation 2: drop the mark check in `restoreOnLeave`. "restores only a fold that the IDE made" turns red.
 * Mutation 3: ignore `dockHolds`. "does not reopen the nav that the dock rule folds" turns red.
 * Mutation 4: make `markFolded` write nothing. "keeps the mark across a reload" turns red.
 */
import { describe, expect, it } from "vitest";

import { clearFoldMark, foldMarked, foldOnOpen, markFolded, restoreOnLeave } from "./ideFold";

function store() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  } as unknown as Storage;
}

describe("foldOnOpen", () => {
  it("folds only when the auto fold is on and the nav is open", () => {
    expect(foldOnOpen({ collapsed: false, enabled: true })).toBe(true);
    expect(foldOnOpen({ collapsed: false, enabled: false })).toBe(false);
    expect(foldOnOpen({ collapsed: true, enabled: true })).toBe(false);
  });
});

describe("the mark", () => {
  it("keeps the mark across a reload, and clears it", () => {
    const s = store();
    expect(foldMarked(s)).toBe(false);
    markFolded(s);
    expect(foldMarked(s)).toBe(true);
    clearFoldMark(s);
    expect(foldMarked(s)).toBe(false);
  });
  it("never throws when storage fails", () => {
    const bad = { getItem: () => { throw new Error("x"); }, setItem: () => { throw new Error("x"); }, removeItem: () => { throw new Error("x"); } } as unknown as Storage;
    expect(() => { markFolded(bad); clearFoldMark(bad); foldMarked(bad); }).not.toThrow();
  });
});

describe("restoreOnLeave", () => {
  it("restores only a fold that the IDE made, and only while the nav is folded", () => {
    expect(restoreOnLeave({ marked: true, collapsed: true, dockHolds: false })).toBe(true);
    expect(restoreOnLeave({ marked: false, collapsed: true, dockHolds: false })).toBe(false);
    expect(restoreOnLeave({ marked: true, collapsed: false, dockHolds: false })).toBe(false);
  });
  it("does not reopen the nav that the dock rule folds", () => {
    expect(restoreOnLeave({ marked: true, collapsed: true, dockHolds: true })).toBe(false);
  });
});
