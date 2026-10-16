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

  it("leaves no line that the renderer draws as an h1", () => {
    const out = demoteHeadings("# One\nTwo\n===\n  # Three");
    expect(out).not.toMatch(/^\s{0,3}#(?!#)/m);
    expect(out).not.toMatch(/\n\s{0,3}=+\s*$/m);
  });
});
