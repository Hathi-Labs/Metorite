/**
 * The hover preview of a page chip (WS-61 KB-2k2): the text extractor. The
 * browser half is `e2e/knowledge-shortcuts.spec.ts`.
 */
import { describe, expect, it } from "vitest";

import { PREVIEW_CHARS, PREVIEW_DELAY_MS, plainText, previewText } from "./chipPreview";

describe("plainText", () => {
  it("strips the Markdown marks", () => {
    const md = "# Title\n\nSome **bold** and _soft_ text with a [link](/knowledge/pages/x) and `code`.\n\n- one\n- [ ] two\n> quote";
    expect(plainText(md)).toBe("Title Some bold and soft text with a link and code. one two quote");
  });

  it("keeps an image's alt, drops tags and fences, and joins lines", () => {
    expect(plainText("![a cat](/x.png)\n<div>hi</div>\n```ts\nlet a = 1;\n```")).toBe("a cat hi let a = 1;");
  });

  it("is empty for nothing", () => {
    expect(plainText(null)).toBe("");
    expect(plainText("  \n\n ")).toBe("");
  });
});

describe("previewText", () => {
  it("shows a short text whole", () => {
    expect(previewText("Refund **policy** for orders.")).toBe("Refund policy for orders.");
  });

  it("cuts a long text at 200 characters, on a word, with an ellipsis", () => {
    const md = Array.from({ length: 80 }, (_, i) => `word${i}`).join(" ");
    const out = previewText(md);
    expect(PREVIEW_CHARS).toBe(200);
    expect(out.endsWith("…")).toBe(true);
    const body = out.slice(0, -1);
    expect(body.length).toBeLessThanOrEqual(200);
    expect(md.startsWith(body)).toBe(true);
    // The cut falls between two words, never inside one.
    expect(md[body.length]).toBe(" ");
  });

  it("cuts a text with no space at the limit", () => {
    expect(previewText("x".repeat(500))).toBe(`${"x".repeat(200)}…`);
  });

  it("keeps a text of exactly 200 characters whole", () => {
    expect(previewText("y".repeat(200))).toBe("y".repeat(200));
  });

  it("waits 400 ms before the card opens", () => {
    expect(PREVIEW_DELAY_MS).toBe(400);
  });
});
