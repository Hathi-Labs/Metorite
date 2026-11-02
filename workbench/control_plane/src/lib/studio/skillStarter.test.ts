// The starter of the skill form (STU-F2b): a pure fill from one sentence.
// Mutation: let `applyStarter` overwrite a typed field, and the empty-field
// tests fail.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  applyStarter,
  starterDescription,
  starterFor,
  starterInstructions,
  starterName,
  type SkillFields,
} from "./skillStarter";

const SENTENCE = "Summarise a long email thread for a busy teammate.";
const EMPTY: SkillFields = { name: "", description: "", instructions: "" };

describe("the starter", () => {
  it("takes the name from the first six words, in sentence case", () => {
    expect(starterName("summarise a long email thread for a busy teammate")).toBe("Summarise a long email thread for");
    expect(starterName("  one   two  ")).toBe("One two");
  });

  it("cuts the name to 120 characters and drops closing punctuation", () => {
    expect(starterName("x".repeat(300)).length).toBe(120);
    expect(starterName("Say hello.")).toBe("Say hello");
  });

  it("writes the description as Use this when, with the sentence in lower case", () => {
    expect(starterDescription(SENTENCE)).toBe("Use this when summarise a long email thread for a busy teammate.");
    expect(starterDescription("y".repeat(2000)).length).toBeLessThanOrEqual(1024);
  });

  it("writes a Markdown skeleton with the three headings", () => {
    const text = starterInstructions(SENTENCE);
    expect(text).toContain("## When to use\n\n" + SENTENCE);
    expect(text).toContain("## Steps\n\n1. ");
    expect(text).toContain("## Rules\n\n- ");
    expect(text.indexOf("## When to use")).toBeLessThan(text.indexOf("## Steps"));
    expect(text.indexOf("## Steps")).toBeLessThan(text.indexOf("## Rules"));
  });

  it("cuts the sentence to 500 characters", () => {
    expect(starterInstructions("z".repeat(900))).toContain("z".repeat(500));
    expect(starterInstructions("z".repeat(900))).not.toContain("z".repeat(501));
  });

  it("gives nothing for a blank sentence", () => {
    expect(starterFor("   ")).toEqual(EMPTY);
  });

  it("picks no action", () => {
    expect(Object.keys(starterFor(SENTENCE)).sort()).toEqual(["description", "instructions", "name"]);
  });
});

describe("applyStarter", () => {
  it("fills every empty field", () => {
    expect(applyStarter(EMPTY, SENTENCE)).toEqual(starterFor(SENTENCE));
  });

  it("never overwrites a field that holds text", () => {
    const typed: SkillFields = { name: "My name", description: "My text", instructions: "My steps" };
    expect(applyStarter(typed, SENTENCE)).toEqual(typed);
  });

  it("fills only the empty ones, and counts a blank as empty", () => {
    const out = applyStarter({ name: "Mine", description: "   ", instructions: "" }, SENTENCE);
    expect(out.name).toBe("Mine");
    expect(out.description).toBe(starterDescription(SENTENCE));
    expect(out.instructions).toBe(starterInstructions(SENTENCE));
  });
});

describe("the module", () => {
  it("imports no fetch and no other module", () => {
    const src = readFileSync(new URL("./skillStarter.ts", import.meta.url), "utf8");
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/\bfetch\b/);
    expect(code).not.toMatch(/^\s*import\s/m);
  });
});
