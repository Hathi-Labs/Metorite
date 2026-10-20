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
import { readColumns, writeColumns } from "./columns";
import { dateLabel, isIsoDate, readDate, todayIso, writeDate } from "./date";
import { escapeHtmlText, escapeInlineText, findClose, unescapeInlineText } from "./htmlFragment";
import { readStatus, writeStatus } from "./status";
import { sortSubpages, writeSubpages } from "./subpages";
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
    if (n.attrs?.align && (n.type === "paragraph" || n.type === "heading")) out.push(`align:${n.attrs.align}`);
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

  it("has a case for each block of KB-2p", () => {
    const nodes = all((c) => c.nodes);
    for (const node of ["status", "kbDate", "columns", "column", "subpages", "pageBreak"]) expect(nodes, node).toContain(node);
  });

  it("has a case for each alignment, on a paragraph and on a heading", () => {
    const aligned = GOLDEN.cases.flatMap((c) => inventory(parseBody(c.saved)).filter((t) => t.startsWith("align:")));
    expect(new Set(aligned)).toEqual(new Set(["align:center", "align:right", "align:justify"]));
    const heading = parseBody('<h2 data-align="right">R</h2>').content?.[0];
    expect(heading).toMatchObject({ type: "heading", attrs: { level: 2, align: "right" } });
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

describe("the inline blocks", () => {
  it("reads and writes a status, and refuses a colour off the list", () => {
    expect(readStatus('<span data-type="status" data-color="green">Done</span> x')).toEqual({
      raw: '<span data-type="status" data-color="green">Done</span>',
      color: "green",
      text: "Done",
    });
    expect(readStatus('<span data-type="status" data-color="teal">X</span>')).toBeNull();
    expect(writeStatus("a*b <c>", "red")).toBe('<span data-type="status" data-color="red">a\\*b &lt;c></span>');
    expect(writeStatus("x", "nope")).toBe('<span data-type="status" data-color="gray">x</span>');
  });

  it("reads a real day only, and labels a pick the same in every locale", () => {
    expect(isIsoDate("2026-10-19")).toBe(true);
    expect(isIsoDate("2026-02-30")).toBe(false);
    expect(isIsoDate("2026-1-5")).toBe(false);
    expect(dateLabel("2026-10-19")).toBe("19 Oct 2026");
    expect(todayIso(new Date(2026, 0, 5))).toBe("2026-01-05");
    expect(readDate('<time data-type="date" datetime="2026-10-19">Monday</time>')?.label).toBe("Monday");
    expect(readDate('<time data-type="date" datetime="2026-10-19"></time>')).toBeNull();
    expect(writeDate("2026-10-19", "")).toBe('<time data-type="date" datetime="2026-10-19">19 Oct 2026</time>');
  });
});

describe("the block forms", () => {
  it("reads columns whose count matches, past a fence and a raw div", () => {
    const md = writeColumns(["A", "```\n</div>\n```", '<div align="center">\nx\n</div>']);
    expect(readColumns(md)?.bodies).toEqual(["A", "```\n</div>\n```", '<div align="center">\nx\n</div>']);
    expect(readColumns(md.replace('data-count="3"', 'data-count="2"'))).toBeNull();
    expect(readColumns('<div data-type="columns" data-count="2">\n\n<div data-type="column">\n\nA\n\n</div>')).toBeNull();
  });

  it("writes an empty column with no body", () => {
    expect(writeColumns(["", "B"])).toBe(
      '<div data-type="columns" data-count="2">\n\n<div data-type="column">\n\n</div>\n\n<div data-type="column">\n\nB\n\n</div>\n\n</div>',
    );
  });

  it("orders the sub-pages by the tree, by title or by the last edit", () => {
    const rows = [
      { id: "a", title: "beta", position: 1, updated_at: "2026-10-01" },
      { id: "b", title: "Alpha", position: 2, updated_at: "2026-10-09" },
      { id: "c", title: "gamma", position: 0, updated_at: null },
    ];
    expect(sortSubpages(rows, "manual").map((r) => r.id)).toEqual(["c", "a", "b"]);
    expect(sortSubpages(rows, "title").map((r) => r.id)).toEqual(["b", "a", "c"]);
    expect(sortSubpages(rows, "recent").map((r) => r.id)).toEqual(["b", "a", "c"]);
    expect(writeSubpages("bogus")).toBe('<div data-type="subpages" data-order="manual"></div>');
  });

  it("keeps a page break, subpages and columns out of a GFM paragraph", () => {
    const doc = parseBody('Text\n<div data-type="page-break"></div>');
    expect(doc.content?.map((n) => n.type)).toEqual(["paragraph", "pageBreak"]);
  });
});
