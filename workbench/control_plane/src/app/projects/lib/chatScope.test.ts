import { describe, expect, it } from "vitest";

import {
  EVERYTHING,
  focusedEntry,
  initialFocus,
  nextFocus,
  scopeOptions,
  type ScopeEntry,
} from "./chatScope";

const ENTRIES: ScopeEntry[] = [
  { id: "s1", name: "Metorite", level: "space", depth: 0 },
  { id: "p1", name: "Projects/Tasks App", level: "project", depth: 1 },
];

describe("the chat's focus", () => {
  it("starts on the node selected last, or on everything", () => {
    expect(initialFocus("p1")).toEqual({ focusId: "p1", pinned: false });
    expect(initialFocus(null).focusId).toBe(EVERYTHING);
  });

  it("follows the tree until the member picks", () => {
    let s = initialFocus("p1");
    s = nextFocus(s, { type: "tree", id: "s1" });
    expect(s.focusId).toBe("s1");
    s = nextFocus(s, { type: "pick", id: EVERYTHING });
    s = nextFocus(s, { type: "tree", id: "p1" });
    // A pick holds: the next click in the tree does not undo "everything".
    expect(s.focusId).toBe(EVERYTHING);
  });

  it("offers everything first, then the tree with its depth", () => {
    const opts = scopeOptions(ENTRIES);
    expect(opts[0]).toEqual({ value: EVERYTHING, label: "Everything you can see" });
    expect(opts[2]).toEqual({ value: "p1", label: "Projects/Tasks App", depth: 1 });
  });

  it("a focus on a node that is gone reads as everything", () => {
    expect(focusedEntry({ focusId: "gone", pinned: true }, ENTRIES)).toBeNull();
    expect(focusedEntry({ focusId: "p1", pinned: false }, ENTRIES)?.name).toBe(
      "Projects/Tasks App",
    );
  });
});
