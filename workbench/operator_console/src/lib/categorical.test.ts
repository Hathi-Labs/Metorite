// Categorical hues — WS-31.
//
// ⚠️ The subject is STABILITY. A categorical colour carries no information, so
// nothing here can be "wrong" in the way a tone can. What it can be is
// INCONSISTENT — the same provider drawing two colours on two pages, or every
// provider silently repainting because somebody added one to a list.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { CAT_SLOTS, categoricalChip, providerGlyph, slotFor } from "./categorical";

const CSS = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");
const RULES = CSS.replace(/\/\*[\s\S]*?\*\//g, " ");

describe("slot assignment", () => {
  it("is deterministic", () => {
    expect(slotFor("anthropic")).toBe(slotFor("anthropic"));
  });

  it("🔴 depends on the NAME, never on position", () => {
    // ⚠️ The whole rule. Index assignment repaints every existing item the
    // moment somebody inserts one, and the repaint is silent — yesterday's
    // screenshot and today's page disagree with no code change between them.
    const before = ["openai", "anthropic", "deepseek"].map(slotFor);
    const after = ["groq", "openai", "anthropic", "deepseek"].map(slotFor);
    expect(after.slice(1)).toEqual(before);
  });

  it("is case- and whitespace-insensitive", () => {
    expect(slotFor("  OpenAI ")).toBe(slotFor("openai"));
  });

  it("always lands inside the ramp", () => {
    for (const n of ["openai", "anthropic", "deepseek", "groq", "x", "", "🐋", "a".repeat(200)]) {
      const s = slotFor(n);
      expect(s).toBeGreaterThanOrEqual(0);
      expect(s).toBeLessThan(CAT_SLOTS);
    }
  });

  it("spreads the providers we actually serve across more than one slot", () => {
    // Not a distribution proof — just a guard against a hash that collapses.
    const names = ["openai", "anthropic", "deepseek", "groq", "mistral", "gemini"];
    expect(new Set(names.map(slotFor)).size).toBeGreaterThan(2);
  });
});

describe("the chip", () => {
  it("names a slot globals.css can draw", () => {
    // 🔴 A slot with no rule renders as an unstyled pill — indistinguishable
    // from neutral, and silently wrong.
    for (let i = 0; i < CAT_SLOTS; i++) {
      expect(RULES).toContain(`.chip.cat-${i}`);
    }
  });

  it("defines every ramp token in BOTH themes", () => {
    const light = RULES.slice(RULES.indexOf('[data-theme="light"]'));
    for (let i = 1; i <= CAT_SLOTS; i++) {
      expect(RULES).toContain(`--cat-${i}:`);
      expect(light).toContain(`--cat-${i}:`);
    }
  });

  it("carries the base chip class too", () => {
    expect(categoricalChip("openai")).toMatch(/^chip cat-[0-7]$/);
  });
});

describe("the glyph", () => {
  it("is a LETTER, not an emoji", () => {
    // ⚠️ The deleted customer table used 🐋 for deepseek and 🦙 for ollama.
    // Emoji render at different sizes on Windows, macOS and Linux, which in a
    // row of chips produces visibly ragged alignment.
    expect(providerGlyph("deepseek")).toBe("D");
    expect(providerGlyph("openai")).toBe("O");
  });

  it("never returns an empty string", () => {
    // An empty glyph collapses the box it sits in and the chip jumps.
    expect(providerGlyph("")).toBe("?");
    expect(providerGlyph("   ")).toBe("?");
  });
});
