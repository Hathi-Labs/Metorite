/**
 * The `/` menu's list (WS-61 KB-2b). The e2e spec inserts each block in a
 * browser (`e2e/knowledge-pages.spec.ts`). This holds the list and the match.
 */
import { describe, expect, it } from "vitest";

import { BLOCKS, BLOCK_GROUPS, blockScore, languageLabel, languageOptions, matchBlocks } from "./blocks";
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

describe("the groups of the / menu", () => {
  it("puts every block under one of the five headings, in heading order", () => {
    expect(BLOCK_GROUPS).toEqual(["Basic blocks", "Lists", "Media", "Advanced", "Inline"]);
    let last = -1;
    for (const b of BLOCKS) {
      const g = BLOCK_GROUPS.indexOf(b.group ?? ("none" as never));
      expect(g, b.id).toBeGreaterThanOrEqual(0);
      expect(g, `${b.id} follows its group`).toBeGreaterThanOrEqual(last);
      last = g;
    }
  });

  it("gives every block a line, and the Markdown shortcuts the owner named", () => {
    for (const b of BLOCKS) expect(b.description, b.id).toBeTruthy();
    const by = (id: string) => BLOCKS.find((b) => b.id === id)!;
    expect(by("h1").shortcut).toBe("#");
    expect(by("h2").shortcut).toBe("##");
    expect(by("bullet").shortcut).toBe("-");
    expect(by("ordered").shortcut).toBe("1.");
    expect(by("task").shortcut).toBe("[]");
    expect(by("quote").shortcut).toBe(">");
    expect(by("code").shortcut).toBe("```");
    expect(by("divider").shortcut).toBe("---");
    expect(by("h1").description).toBe("Big section heading");
  });

  it("makes Link to a page the @ menu, and keeps [[ as a word to find it", () => {
    const page = BLOCKS.find((b) => b.id === "page")!;
    expect(page).toMatchObject({ label: "Link to a page", kind: "pageLink", group: "Inline" });
    expect(page.description).toBe("Mention a page or a date");
    expect(matchBlocks("[[").map((b) => b.id)).toContain("page");
    expect(matchBlocks("mention")[0].id).toBe("page");
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
