/**
 * The shared categorical vocabulary.
 *
 * Two properties, and they pull against each other, which is why both are
 * pinned: the assignment has to be STABLE (a name keeps its colour across
 * sessions, users and deploys, because nothing is stored) and the ramp has to
 * be USED WHOLE (a hash that quietly favoured three slots would still be
 * stable, and would still make categories look alike — the one thing this
 * exists to prevent).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { CATEGORICAL_TOKENS } from "@/lib/theme/types";

import {
  CATEGORICAL_ACCENTS,
  CATEGORICAL_SLOTS,
  HASH_SLOTS,
  accentForSlot,
  categoricalAccent,
  hashSlot,
} from "./categorical";

describe("the ramp's class vocabulary", () => {
  it("has one accent per themed slot", () => {
    expect(CATEGORICAL_SLOTS).toBe(CATEGORICAL_TOKENS.length);
    expect(CATEGORICAL_ACCENTS).toHaveLength(CATEGORICAL_TOKENS.length);
  });

  it("names each slot's own token, in slot order", () => {
    // Slot n must draw --cat-(n+1). An off-by-one here is invisible on screen
    // — every colour is still a valid colour — and permanently wrong.
    CATEGORICAL_TOKENS.forEach((token, i) => {
      expect(CATEGORICAL_ACCENTS[i].dot).toBe(`bg-${token}`);
      expect(CATEGORICAL_ACCENTS[i].chip).toBe(
        `border-${token}/30 bg-${token}/10 text-${token}`,
      );
    });
  });

  it("emits only themed classes", () => {
    // The regression this module was extracted for: raw palette hues that
    // survive a theme switch while the surface around them moves.
    for (const { chip, dot } of CATEGORICAL_ACCENTS) {
      for (const cls of `${chip} ${dot}`.split(" ")) {
        expect(cls, cls).toMatch(/^(?:border|bg|text)-cat-(?:[1-9]|1[0-2])(?:\/\d+)?$/);
      }
    }
  });

  it("writes its classes literally, so Tailwind's scanner can see them", () => {
    // A template-built `bg-cat-${n}` type-checks, renders a plausible class
    // attribute, and produces no CSS at all. Reading our own source is the
    // only way to assert the difference.
    // Comments stripped first — the module's own doc comment quotes the
    // interpolated form as the thing NOT to write, and that is not code.
    const src = readFileSync(
      fileURLToPath(new URL("./categorical.ts", import.meta.url)),
      "utf8",
    )
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(?<![:"'/])\/\/[^\n]*/g, "");
    for (const token of CATEGORICAL_TOKENS) {
      expect(src, token).toContain(`bg-${token}/10`);
      expect(src, token).toContain(`text-${token}`);
    }
    expect(src, "no interpolated class names").not.toMatch(/["'`](?:bg|text|border)-cat-\$\{/);
  });
});

describe("accentForSlot", () => {
  it("is total — any integer lands on the ramp", () => {
    // Callers derive slots from hand-written maps and from arithmetic. An
    // out-of-range slot must not render `undefined` class strings.
    for (const slot of [-9, -1, 0, 7, 8, 99, 1_000_003]) {
      expect(accentForSlot(slot), String(slot)).toBeDefined();
      expect(CATEGORICAL_ACCENTS).toContain(accentForSlot(slot));
    }
  });
});

describe("hashSlot", () => {
  it("is stable for a name", () => {
    // Nothing is persisted, so "stable" means "recomputed identically". If
    // this ever needs to change, every existing category changes colour.
    expect(hashSlot("marketing")).toBe(hashSlot("marketing"));
    expect(hashSlot("marketing")).toBe(hashSlot(" Marketing "));
    expect(hashSlot("marketing")).toBe(hashSlot("MARKETING"));
  });

  it("never lands outside the HASH range — slots 9-12 are choice-only", () => {
    // The freeze this pins: `hash % HASH_SLOTS`, with HASH_SLOTS at the
    // original eight. Widening the modulus repaints every hash-assigned
    // @context and tag at once, silently. A choice-only slot is reachable
    // through `accentForSlot`, never through a hash.
    expect(HASH_SLOTS).toBe(8);
    for (let i = 0; i < 1000; i++) {
      const slot = hashSlot(`label-${i}`);
      expect(slot).toBeGreaterThanOrEqual(0);
      expect(slot).toBeLessThan(HASH_SLOTS);
    }
  });

  it("uses the whole hash range, and roughly evenly", () => {
    // A hash collapsing onto two slots would pass every test above while
    // making categories indistinguishable. 400 names over 8 slots averages 50;
    // anything under 20 is a real skew, not sampling noise.
    const counts = new Array(HASH_SLOTS).fill(0);
    for (let i = 0; i < 400; i++) counts[hashSlot(`label-${i}`)]++;
    expect(counts.filter((n) => n > 0)).toHaveLength(HASH_SLOTS);
    expect(Math.min(...counts)).toBeGreaterThan(20);
  });
});

describe("categoricalAccent", () => {
  it("gives two different names different accents more often than not", () => {
    const accents = new Set(
      Array.from({ length: 200 }, (_, i) => categoricalAccent(`label-${i}`).chip),
    );
    expect(accents.size).toBe(HASH_SLOTS);
  });
});
