/**
 * Which task-panel sections a member has folded away.
 *
 * Owner directive, 2026-09-19: the sections should collapse, so a task with
 * many files or comments stays neat. These are the rules that survive a
 * reload, and every case is a way the preference could quietly lose or
 * invent a fold.
 */

import { describe, expect, it } from "vitest";

import {
  FOLD_STORAGE_KEY,
  type FoldStore,
  isFoldable,
  parseFolded,
  readFolded,
  serialiseFolded,
  toggleFold,
  writeFolded,
} from "./sectionFold";

const fakeStore = (data: Record<string, string> = {}) => {
  const store = {
    data,
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v;
    },
  };
  return store as FoldStore & { data: Record<string, string> };
};

describe("toggleFold", () => {
  it("folds and unfolds one section", () => {
    const folded = toggleFold(new Set(), "files", false);
    expect([...folded]).toEqual(["files"]);
    expect([...toggleFold(folded, "files", true)]).toEqual([]);
  });

  it("returns a NEW set, because React state must not be mutated", () => {
    const before = new Set<string>();
    const after = toggleFold(before, "activity", false);
    expect(after).not.toBe(before);
    expect(before.size).toBe(0);
  });

  it("leaves the other sections alone", () => {
    const folded = toggleFold(new Set(["files"]), "activity", false);
    expect([...folded].sort()).toEqual(["activity", "files"]);
  });
});

describe("parseFolded", () => {
  it("reads what was written", () => {
    expect([...parseFolded('["files","activity"]')].sort()).toEqual([
      "activity",
      "files",
    ]);
  });

  it("⚠️ drops ids that are no longer sections", () => {
    // A section removed from the product would otherwise sit in everybody's
    // storage forever, and a typo from an older build would fold nothing
    // while looking like it should.
    expect([...parseFolded('["files","gone","also-gone"]')]).toEqual(["files"]);
  });

  it("survives anything storage can hold", () => {
    for (const raw of [null, "", "not json", "{}", '"files"', "[1,2]"]) {
      expect([...parseFolded(raw)]).toEqual([]);
    }
  });
});

describe("the stored value is the FOLDED set", () => {
  it("⚠️ an absent entry means OPEN, not closed", () => {
    // Storing the OPEN set instead would make every new section default to
    // collapsed the moment it ships, and a member who never touched this
    // preference would find parts of the panel missing.
    expect([...readFolded(fakeStore())]).toEqual([]);
  });

  it("round-trips through the store", () => {
    const store = fakeStore();
    writeFolded(new Set(["files", "activity"]), store);
    expect([...readFolded(store)].sort()).toEqual(["activity", "files"]);
    expect(store.data[FOLD_STORAGE_KEY]).toBe('["activity","files"]');
  });

  it("serialises in a stable order, so the value does not churn", () => {
    expect(serialiseFolded(new Set(["files", "activity"]))).toBe(
      serialiseFolded(new Set(["activity", "files"])),
    );
  });

  it("does nothing at all when there is no store", () => {
    // A private window, a blocked frame, or the server render.
    expect([...readFolded(null)]).toEqual([]);
    expect(() => writeFolded(new Set(["files"]), null)).not.toThrow();
  });

  it("never writes an id that is not a section", () => {
    const store = fakeStore();
    writeFolded(new Set(["files", "made-up"]), store);
    expect(store.data[FOLD_STORAGE_KEY]).toBe('["files"]');
  });
});

describe("isFoldable", () => {
  it("knows the six sections and nothing else", () => {
    for (const id of ["details", "description", "properties", "links", "files", "activity"]) {
      expect(isFoldable(id)).toBe(true);
    }
    for (const id of ["", "Files", null, undefined, 7]) {
      expect(isFoldable(id)).toBe(false);
    }
  });
});
