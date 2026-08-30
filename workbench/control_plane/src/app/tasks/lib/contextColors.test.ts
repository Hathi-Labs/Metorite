/**
 * The @context → ramp-slot assignment.
 *
 * A context's colour is not stored anywhere: it is recomputed from the name on
 * every render, on every machine, for every member. That is only a feature
 * while the computation is FROZEN. Change the hash, reassign a `KEYWORD` index,
 * resize the ramp, or "tidy" the normalisation, and every @context in the
 * product silently changes colour for everybody at once — with nothing failing,
 * because the new colours are just as valid as the old ones.
 *
 * So the assignment is pinned here as a golden table rather than described. The
 * table is the contract; if you have a reason to break it, the failure is the
 * conversation. The ramp's own behaviour — the class strings, the hash's
 * spread, out-of-range slots — belongs to `src/lib/categorical.test.ts`.
 */

import { describe, expect, it } from "vitest";

import { CATEGORICAL_SLOTS, HASH_SLOTS } from "@/lib/categorical";

import { contextAccent, contextSlot } from "./contextColors";

/** Frozen 2026-08-10. Do not regenerate to make a change pass. */
const GOLDEN: Record<string, number> = {
  // Keyword-mapped: the common GTD contexts, assigned by hand.
  "@computer": 0,
  "@agenda": 1,
  "@home": 2,
  "@errands": 3,
  "@calls": 4,
  "@email": 5,
  "@office": 6,
  "@read": 7,
  // Hashed: everything else.
  "@gym": 2,
  "@garage": 4,
  "@bank": 1,
  "@anywhere": 0,
  "@deep-work": 3,
  "@shopping": 5,
  "@school": 5,
  "@studio": 5,
};

describe("contextSlot", () => {
  it.each(Object.entries(GOLDEN))("%s keeps slot %i", (name, slot) => {
    expect(contextSlot(name)).toBe(slot);
  });

  it("is stable across the surface spellings of the same context", () => {
    // The call sites pass what the store holds, what a facet key holds and what
    // a user typed. A chip and its own filter option disagreeing on colour is
    // the bug this normalisation prevents — and `" @office "` really did miss
    // KEYWORD and hash elsewhere, because the sigil was stripped before the
    // trim rather than after.
    for (const spelling of ["@Office", "office", " @office ", "@OFFICE"]) {
      expect(contextSlot(spelling), spelling).toBe(contextSlot("@office"));
    }
  });

  it("keeps every hand-assigned context on its own slot", () => {
    // The eight KEYWORD groups were chosen to be mutually distinguishable; two
    // of them landing on one slot would quietly undo that.
    const common = ["@computer", "@agenda", "@home", "@errands", "@calls", "@email", "@office", "@read"];
    const slots = common.map(contextSlot);
    expect(new Set(slots).size).toBe(common.length);
    // HASH_SLOTS, not the full ramp: contexts hash, and the hash is frozen
    // at eight so nothing repaints — slots 9-12 are choice-only.
    expect(new Set(slots).size).toBe(HASH_SLOTS);
  });

  it("puts an unknown context somewhere on the ramp", () => {
    for (let i = 0; i < 200; i++) {
      const slot = contextSlot(`@ctx-${i}`);
      expect(slot).toBeGreaterThanOrEqual(0);
      expect(slot).toBeLessThan(CATEGORICAL_SLOTS);
    }
  });
});

describe("contextAccent", () => {
  it("draws only themed ramp classes", () => {
    // The regression this file was written for: eight raw Tailwind palette hues
    // that survived a theme switch while everything around them changed.
    for (const name of [...Object.keys(GOLDEN), "@whatever", "@x"]) {
      const { chip, dot } = contextAccent(name);
      for (const cls of `${chip} ${dot}`.split(" ")) {
        expect(cls, `${name} → ${cls}`).toMatch(/^(?:border|bg|text)-cat-[1-8](?:\/\d+)?$/);
      }
    }
  });

  it("gives the eight common contexts eight different chips", () => {
    const chips = new Set(
      ["@computer", "@agenda", "@home", "@errands", "@calls", "@email", "@office", "@read"].map(
        (n) => contextAccent(n).chip,
      ),
    );
    expect(chips.size).toBe(HASH_SLOTS);
  });
});
