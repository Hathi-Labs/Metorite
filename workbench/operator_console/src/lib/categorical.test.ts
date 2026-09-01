// Categorical hues — WS-31.
//
// ⚠️ The subject is STABILITY. A categorical colour carries no information, so
// nothing here can be "wrong" in the way a tone can. What it can be is
// INCONSISTENT — the same provider drawing two colours on two pages, or every
// provider silently repainting because somebody added one to a list.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  CAT_SLOTS,
  categoricalBox,
  categoricalChip,
  providerGlyph,
  slotFor,
} from "./categorical";

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

  // 🔴 The bug this fences: `.glyph` painted `background: currentColor`
  // while also setting `color: var(--panel)` — and currentColor resolves
  // against the element's OWN color, not the parent chip's hue. Result:
  // a panel-on-panel box, i.e. a blank tile on every vendor card, invisible
  // to every unit test because no renderer runs here. The fix routes the
  // parent's hue through `--hue`, which each cat slot must therefore define.
  it("takes its box colour from --hue, never from currentColor", () => {
    const glyph = RULES.slice(RULES.indexOf(".chip .glyph"));
    const rule = glyph.slice(0, glyph.indexOf("}"));
    expect(rule).toContain("background: var(--hue");
    expect(rule).not.toContain("background: currentColor");
  });

  it("every cat slot defines the --hue the glyph consumes", () => {
    for (let i = 0; i < CAT_SLOTS; i++) {
      const at = RULES.indexOf(`.chip.cat-${i} `);
      expect(at).toBeGreaterThan(-1);
      const rule = RULES.slice(at, RULES.indexOf("}", at));
      expect(rule).toContain("--hue: var(--cat-");
    }
  });
});

describe("the organization monogram box", () => {
  it("wears the SAME slot as the chip would — one name, one colour", () => {
    for (const n of ["Fracktal Works", "aster", "orrery"]) {
      expect(categoricalBox(n)).toBe(`orgglyph cat-${slotFor(n)}`);
    }
  });

  it("every slot has a box rule defining its --hue", () => {
    // The box reads var(--hue) with a NEUTRAL fallback, so a slot with no
    // rule degrades to a grey box — visible, never invisible (the
    // currentColor lesson, applied in advance).
    for (let i = 0; i < CAT_SLOTS; i++) {
      const at = RULES.indexOf(`.orgglyph.cat-${i}`);
      expect(at).toBeGreaterThan(-1);
      const rule = RULES.slice(at, RULES.indexOf("}", at));
      expect(rule).toContain("--hue: var(--cat-");
    }
    const box = RULES.slice(RULES.indexOf(".orgglyph {"));
    const rule = box.slice(0, box.indexOf("}"));
    expect(rule).toContain("background: var(--hue");
    expect(rule).not.toContain("background: currentColor");
  });

  it("the roster and the detail hero both draw it", () => {
    const table = readFileSync(
      join(__dirname, "..", "app", "CustomerTable.tsx"), "utf8");
    const detail = readFileSync(
      join(__dirname, "..", "app", "customers", "[slug]", "page.tsx"), "utf8");
    expect(table).toContain("categoricalBox(");
    expect(detail).toContain("categoricalBox(");
  });
});
