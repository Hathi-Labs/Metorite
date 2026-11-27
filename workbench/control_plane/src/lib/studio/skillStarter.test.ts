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
    expect(starterName("summarise a long email thread for a busy teammate")).toBe("Summarise a long email thread");
    expect(starterName("  one   two  ")).toBe("One two");
  });

  it("cuts the name to 120 characters and drops closing punctuation", () => {
    expect(starterName("x".repeat(300)).length).toBe(120);
    expect(starterName("Say hello.")).toBe("Say hello");
  });

  it("drops a closing stop-word after the cut to six words", () => {
    expect(starterName("Send a weekly digest of the open tasks")).toBe("Send a weekly digest");
    expect(starterName("Plan the week with")).toBe("Plan the week");
    expect(starterName("Clean the inbox for")).toBe("Clean the inbox");
  });

  it("joins a clause to Use this (when, whenever, if, after, before, once)", () => {
    expect(starterDescription("When I get a long thread.")).toBe("Use this when I get a long thread");
    expect(starterDescription("If a deal goes quiet")).toBe("Use this if a deal goes quiet");
    expect(starterDescription("After a call ends")).toBe("Use this after a call ends");
  });

  it("joins a sentence that starts with to", () => {
    expect(starterDescription("To summarise a long email thread")).toBe("Use this to summarise a long email thread");
  });

  it("joins a noun phrase with for", () => {
    expect(starterDescription("A weekly report of open tasks.")).toBe("Use this for a weekly report of open tasks");
    expect(starterDescription("My inbox at the end of a day")).toBe("Use this for my inbox at the end of a day");
  });

  it("joins an imperative verb with to, and strips one full stop", () => {
    expect(starterDescription(SENTENCE)).toBe("Use this to summarise a long email thread for a busy teammate");
    expect(starterDescription("Triage the inbox..")).toBe("Use this to triage the inbox.");
    expect(starterDescription("y".repeat(2000)).length).toBeLessThanOrEqual(1024);
  });

  it("keeps PDFs, Gmail and I as they are", () => {
    expect(starterDescription("PDFs that need a summary")).toBe("Use this for PDFs that need a summary");
    expect(starterDescription("Gmail threads that need a reply")).toBe("Use this for Gmail threads that need a reply");
    expect(starterDescription("I get a long thread")).toBe("Use this when I get a long thread");
    expect(starterDescription("I'm out of the office")).toBe("Use this when I'm out of the office");
    expect(starterDescription("Summarise my Gmail and PDFs")).toBe("Use this to summarise my Gmail and PDFs");
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
