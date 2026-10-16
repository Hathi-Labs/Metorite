/**
 * A page body draws no second `<h1>` (WS-61 KB-2a). Fence for `markdown.ts`.
 */
import { describe, expect, it } from "vitest";

import { demoteHeadings } from "./markdown";

describe("demoteHeadings", () => {
  it("moves each ATX heading one level down", () => {
    expect(demoteHeadings("# A\n\n## B\n\n### C")).toBe("## A\n\n### B\n\n#### C");
  });

  it("keeps the deepest level, and leaves text that is not a heading", () => {
    expect(demoteHeadings("###### F")).toBe("###### F");
    expect(demoteHeadings("#hashtag and C# code")).toBe("#hashtag and C# code");
  });

  it("turns a setext h1 into a setext h2", () => {
    expect(demoteHeadings("Title\n=====\n\nText")).toBe("Title\n-----\n\nText");
  });

  it("leaves a code fence alone", () => {
    const md = "```\n# a comment\nTitle\n===\n```\n# Real";
    expect(demoteHeadings(md)).toBe("```\n# a comment\nTitle\n===\n```\n## Real");
  });

  it("moves a heading inside a quote or a list item down too", () => {
    expect(demoteHeadings("> # Quoted")).toBe("> ## Quoted");
    expect(demoteHeadings("- # Listed")).toBe("- ## Listed");
    expect(demoteHeadings("1. # Num")).toBe("1. ## Num");
    expect(demoteHeadings("> - # Deep")).toBe("> - ## Deep");
    expect(demoteHeadings("> Quoted title\n> ===")).toBe("> Quoted title\n> ---");
  });

  it("leaves a list item or a quote that holds no heading", () => {
    expect(demoteHeadings("- item\n> quote\n2. step")).toBe("- item\n> quote\n2. step");
  });

  it("leaves no line that the renderer draws as an h1", () => {
    const out = demoteHeadings("# One\nTwo\n===\n  # Three");
    expect(out).not.toMatch(/^\s{0,3}#(?!#)/m);
    expect(out).not.toMatch(/\n\s{0,3}=+\s*$/m);
  });
});
