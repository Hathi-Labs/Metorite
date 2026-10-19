/**
 * The `/` menu's list (WS-61 KB-2b). The e2e spec inserts each block in a
 * browser (`e2e/knowledge-pages.spec.ts`). This holds the list and the match.
 */
import { describe, expect, it } from "vitest";

import { BLOCKS, blockScore, languageLabel, languageOptions, matchBlocks } from "./blocks";
import { CALLOUT_TONES } from "./schema";

describe("the blocks of the / menu", () => {
  it("offers each block of the set once", () => {
    const ids = BLOCKS.map((b) => b.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ["text", "h1", "h2", "h3", "bullet", "ordered", "task", "quote", "toggle", "code", "table", "divider", "image", "page"]) {
      expect(ids).toContain(id);
    }
    for (const tone of CALLOUT_TONES) expect(ids).toContain(`callout-${tone}`);
  });

  it("gives every insert block an act, and the two others a kind the editor handles", () => {
    for (const b of BLOCKS) {
      if (b.kind === "insert") expect(typeof b.run, b.id).toBe("function");
      else expect(["image", "pageLink"]).toContain(b.kind);
    }
  });

  it("matches a label start first, then a word or a keyword, then letters in order", () => {
    expect(matchBlocks("head").map((b) => b.id).slice(0, 3)).toEqual(["h1", "h2", "h3"]);
    expect(matchBlocks("todo")[0].id).toBe("task");
    expect(matchBlocks("warn")[0].id).toBe("callout-warning");
    expect(matchBlocks("hr")[0].id).toBe("divider");
    expect(matchBlocks("tbl").map((b) => b.id)).toContain("table");
    expect(matchBlocks("zzzz")).toEqual([]);
  });

  it("lists every block, in order, for no words", () => {
    expect(matchBlocks("").map((b) => b.id)).toEqual(BLOCKS.map((b) => b.id));
    expect(blockScore(BLOCKS[0], "  ")).toBe(1);
  });
});

describe("the languages of a code block", () => {
  it("keeps a language the list does not know, so a pick never drops it", () => {
    expect(languageOptions("ts")[0]).toEqual({ value: "ts", label: "ts" });
    expect(languageOptions("python").some((l) => l.value === "python")).toBe(true);
    expect(languageOptions(null)[0]).toEqual({ value: "", label: "Plain text" });
  });

  it("names a known language and echoes an unknown one", () => {
    expect(languageLabel("typescript")).toBe("TypeScript");
    expect(languageLabel("ts")).toBe("ts");
    expect(languageLabel("")).toBe("Plain text");
  });
});
