/**
 * The blocks and marks with a stored form (WS-61 KB-2p, `knowledge_base.md`
 * §21.7).
 *
 * `editorMarkdown.test.ts` runs every case of the golden set: the pinned
 * save, the fixed point, the kept blocks and marks, and the CommonMark and
 * GFM meaning. This file holds what is new in KB-2p:
 *
 * - the golden set has a case for each stored form,
 * - only the exact shapes of the §21.7 table read as a block or a mark, and
 *   every other HTML stays raw text,
 * - a stored colour reaches a style only as a checked hex value.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { getSchema, type JSONContent } from "@tiptap/core";
import { describe, expect, it } from "vitest";

import { HIGHLIGHT_COLORS, TEXT_COLORS, hexStyle, isHexColor, isHighlightColor, isTextColor } from "./colors";
import { escapeHtmlText, escapeInlineText, findClose, unescapeInlineText } from "./htmlFragment";
import { normaliseBody, parseBody } from "./markdown";
import { knowledgeExtensions } from "./schema";

interface GoldenCase {
  name: string;
  md: string;
  saved: string;
  nodes: string[];
  marks: string[];
}

const GOLDEN = JSON.parse(
  readFileSync(fileURLToPath(new URL("../../../../../../../tests/fixtures/kb_editor_golden.json", import.meta.url)), "utf8"),
) as { cases: GoldenCase[] };

const all = (pick: (c: GoldenCase) => string[]) => new Set(GOLDEN.cases.flatMap(pick));

/** Every node and mark of a parsed body, as `type` and `type.attr=value`. */
function inventory(doc: JSONContent): string[] {
  const out: string[] = [];
  const walk = (n: JSONContent) => {
    out.push(n.type ?? "?");
    for (const m of n.marks ?? []) out.push(`${m.type}:${JSON.stringify(m.attrs ?? {})}`);
    for (const c of n.content ?? []) walk(c);
  };
  walk(doc);
  return out;
}

describe("the golden set holds each stored form", () => {
  it("has a case for each mark of KB-2p", () => {
    const marks = all((c) => c.marks);
    for (const mark of ["textColor", "highlight", "superscript", "subscript"]) expect(marks, mark).toContain(mark);
  });

  it("keeps each KB-2p case a fixed point that the first save does not change", () => {
    const cases = GOLDEN.cases.filter((c) => c.name.startsWith("kb2p-"));
    expect(cases.length).toBeGreaterThan(5);
    for (const c of cases) {
      expect(c.saved, c.name).toBe(c.md);
      expect(normaliseBody(c.saved), c.name).toBe(c.saved);
    }
  });
});

describe("only the exact shapes read as a stored form", () => {
  it.each([
    ['<span data-color="url(x)">x</span>', "a colour that is not on the list"],
    ['<span data-color="blue" class="x">x</span>', "an attribute more"],
    ["<span data-color='blue'>x</span>", "single quotes"],
    ['<span data-color="#12345">x</span>', "a hex of five digits"],
    ['<mark>x</mark>', "a highlight with no colour"],
    ['<sup class="a">x</sup>', "a sup with an attribute"],
    ['<span data-color="blue"></span>', "an empty fragment"],
    ['<span data-color="blue">never closed', "no close tag"],
  ])("keeps %s as raw text (%s)", (md) => {
    const doc = parseBody(`a ${md} b`);
    const found = inventory(doc);
    expect(found.some((t) => /^(textColor|highlight|superscript|subscript):/.test(t))).toBe(false);
    expect(normaliseBody(`a ${md} b`)).toBe(`a ${md} b`);
  });

  it("reads a colour mark around Markdown, and a mark inside bold", () => {
    expect(inventory(parseBody('<span data-color="blue">**b**</span>'))).toContain('bold:{}');
    expect(inventory(parseBody('**a <mark data-color="green">b</mark>**'))).toContain('highlight:{"color":"green"}');
  });

  it("finds the close tag past a nested tag of the same name and past code", () => {
    const src = '<span data-color="a">x <span class="y">z</span> `</span>` w</span> tail';
    const open = '<span data-color="a">'.length;
    expect(src.slice(findClose(src, open, "span"))).toBe("</span> tail");
    expect(findClose("<sup>never", 5, "sup")).toBe(-1);
  });
});

describe("a stored colour reaches a style only as a checked hex", () => {
  it("accepts a name of the list and a hex, and refuses the rest", () => {
    for (const c of TEXT_COLORS) expect(isTextColor(c.name)).toBe(true);
    for (const c of HIGHLIGHT_COLORS) expect(isHighlightColor(c.name)).toBe(true);
    expect(isHexColor("#2563eb")).toBe(true);
    expect(isHexColor("#ABC")).toBe(true);
    for (const bad of ["red;background:url(x)", "#2563eb;", "expression(1)", "", null, "#12"]) {
      expect(isTextColor(bad), String(bad)).toBe(false);
      expect(isHighlightColor(bad), String(bad)).toBe(false);
    }
    expect(hexStyle("color", "#2563eb")).toBe("color: #2563eb");
    expect(hexStyle("color", "blue")).toBeNull();
    expect(hexStyle("color", "red; x: y")).toBeNull();
  });

  it("draws a name with no style, a hex with its style, and a bad value with neither", () => {
    const schema = getSchema(knowledgeExtensions());
    const spec = (type: string, color: string) => {
      const mark = schema.marks[type].create({ color });
      const out = schema.marks[type].spec.toDOM?.(mark, true) as [string, Record<string, string>];
      return out[1];
    };
    expect(spec("textColor", "blue")).toEqual({ "data-color": "blue", class: "kb-text-color" });
    expect(spec("textColor", "#2563eb")).toEqual({ "data-color": "#2563eb", style: "color: #2563eb", class: "kb-text-color" });
    expect(spec("highlight", "#fde68a").style).toBe("background-color: #fde68a");
    expect(spec("textColor", "red;background:url(x)")).toEqual({ class: "kb-text-color" });
    expect(spec("highlight", "javascript:x")).toEqual({ class: "kb-highlight" });
  });
});

describe("the text of a fragment", () => {
  it("escapes only what would end the text, and reads it back", () => {
    expect(escapeHtmlText("5 < 6 & </span> &copy;")).toBe("5 < 6 & &lt;/span> &amp;copy;");
    expect(unescapeInlineText(escapeInlineText("a *b* [c] </x> &amp;"))).toBe("a *b* [c] </x> &amp;");
  });
});
