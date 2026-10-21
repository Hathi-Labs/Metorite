/**
 * The block editor's Markdown (WS-61 KB-2b, `knowledge_base.md` §21.5).
 *
 * "No loss" is a fixed point: parse and write once gives md′, and md′
 * parsed and written again gives md′ byte for byte, with every block, every
 * mark and every word of the set kept. The golden set lives in
 * `tests/fixtures/kb_editor_golden.json`, and pytest runs the chunker on the
 * same `saved` bodies (`tests/unit/test_kb_editor_golden.py`).
 *
 * The editor itself needs a DOM, and vitest here runs in node. So these
 * tests drive the one `MarkdownManager` the editor uses, and the schema the
 * editor builds from the same list (`knowledgeExtensions`).
 *
 * The editor reads Markdown with marked, which is looser than the specs. So
 * each saved body is also read with a CommonMark and GFM reader
 * (`gfmShape.ts`, micromark), and it must mean what the stored body meant.
 * A case that the editor normalises on purpose names why in `gfmDiffers`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  getExtensionField,
  getSchema,
  getSchemaByResolvedExtensions,
  getSchemaTypeByName,
  resolveExtensions,
  type JSONContent,
} from "@tiptap/core";
import { describe, expect, it } from "vitest";

import { gfmShape } from "./gfmShape";
import { codeSpanFence, decodeEntities, markdownTitle } from "./inline";
import { isLoose, separateAdjacentLists } from "./lists";
import { bodyAfterEdit, normaliseBody, parseBody, wrapInlineRuns, writeBody } from "./markdown";
import {
  codeFence,
  displayHeadingLevel,
  escapeCellPipes,
  escapeLineStart,
  escapeSummary,
  isLineBreakHtml,
  knowledgeExtensions,
  markdownDestination,
  unescapeSummary,
} from "./schema";

interface GoldenCase {
  name: string;
  md: string;
  saved: string;
  nodes: string[];
  marks: string[];
  /** Set only when the editor normalises the shape on purpose: the reason. */
  gfmDiffers?: string;
}

const GOLDEN = JSON.parse(
  readFileSync(fileURLToPath(new URL("../../../../../../../tests/fixtures/kb_editor_golden.json", import.meta.url)), "utf8"),
) as { cases: GoldenCase[] };

/** The node types in reading order, and the marks, and the words. */
function shape(doc: JSONContent): { order: string[]; marks: string[]; text: string } {
  const order: string[] = [];
  const marks: string[] = [];
  let text = "";
  const walk = (n: JSONContent) => {
    order.push(n.type ?? "?");
    for (const m of n.marks ?? []) marks.push(m.type);
    if (n.text) text += ` ${n.text} `;
    if (n.type === "htmlInline") text += String(n.attrs?.raw ?? "");
    if (n.type === "image") text += `[${n.attrs?.alt ?? ""}|${n.attrs?.src ?? ""}|${n.attrs?.title ?? ""}]`;
    if (n.type === "codeBlock") text += `{${n.attrs?.language ?? ""}}`;
    if (n.type === "callout") text += `{${n.attrs?.tone ?? ""}}`;
    if (n.type === "taskItem") text += n.attrs?.checked ? "[x]" : "[ ]";
    if (n.type === "footnoteReference" || n.type === "footnoteDefinition") text += `{^${n.attrs?.label ?? ""}}`;
    if (n.type === "tableCell" || n.type === "tableHeader") text += `{${n.attrs?.align ?? "-"}}`;
    for (const m of n.marks ?? []) if (m.type === "link") text += `(${m.attrs?.href ?? ""})`;
    for (const c of n.content ?? []) walk(c);
  };
  walk(doc);
  // The words count, and the white space between them does not: a soft
  // break, leading spaces and two spaces all read as one space.
  return { order, marks: marks.sort(), text: text.replace(/\s+/g, " ").trim() };
}

const typesOf = (doc: JSONContent) => {
  const nodes = new Set<string>();
  const marks = new Set<string>();
  const walk = (n: JSONContent) => {
    if (n.type) nodes.add(n.type);
    for (const m of n.marks ?? []) marks.add(m.type);
    for (const c of n.content ?? []) walk(c);
  };
  walk(doc);
  return { nodes: [...nodes].sort(), marks: [...marks].sort() };
};

describe("the golden set round-trips with no loss", () => {
  it("holds a case for each block and mark of the set", () => {
    const all = typesOf({ type: "doc", content: GOLDEN.cases.map((c) => parseBody(c.md)) });
    for (const node of [
      "heading", "paragraph", "bulletList", "orderedList", "taskList", "blockquote", "callout",
      "details", "codeBlock", "table", "horizontalRule", "image", "hardBreak", "htmlBlock", "htmlInline",
      "footnoteReference", "footnoteDefinition",
    ]) {
      expect(all.nodes, node).toContain(node);
    }
    for (const mark of ["bold", "italic", "strike", "code", "link"]) expect(all.marks, mark).toContain(mark);
  });

  it("holds each shape of the fix round", () => {
    const names = new Set(GOLDEN.cases.map((c) => c.name));
    for (const name of [
      "image-in-link", "image-and-text-in-link", "empty-link", "entities-named-numeric", "code-span-backtick",
      "link-destination-space", "link-title-quote", "ordered-child-indent", "ordered-ten",
      "ordered-loose-continuation", "ordered-code", "setext-two-lines", "front-matter", "adjacent-lists",
      "star-bullets-plus", "details-open", "ref-definitions", "task-in-ordered", "footnote",
    ]) {
      expect(names, name).toContain(name);
    }
    // KB-2i (§21.7): the table menu's alignments and header row, and footnotes.
    for (const name of [
      "footnote-named-multi", "footnote-repeat-list", "footnote-middle", "footnote-empty",
      "footnote-in-table-and-list", "table-align-three", "table-header-off", "table-header-off-only-row",
      "table-align-mixed-default",
    ]) {
      expect(names, name).toContain(name);
    }
    expect(names.size).toBe(GOLDEN.cases.length);
    // Only these shapes are normalised on purpose. A new one needs a reason.
    expect(GOLDEN.cases.filter((c) => c.gfmDiffers).map((c) => c.name).sort()).toEqual([
      "code-in-table-backtick",
      "mixed-task",
    ]);
  });

  for (const c of GOLDEN.cases) {
    describe(c.name, () => {
      it("writes the pinned body on the first save", () => {
        expect(normaliseBody(c.md)).toBe(c.saved);
      });

      it("is a fixed point: the saved body writes itself byte for byte", () => {
        expect(normaliseBody(c.saved)).toBe(c.saved);
      });

      it("keeps every block, mark and word", () => {
        const before = parseBody(c.md);
        const after = parseBody(c.saved);
        expect(typesOf(before)).toEqual({ nodes: c.nodes, marks: c.marks });
        expect(shape(after)).toEqual(shape(before));
      });

      it("is read by CommonMark and GFM as the stored body was", () => {
        if (c.gfmDiffers) {
          expect(c.gfmDiffers).toMatch(/^Normalised\. /);
          expect(gfmShape(c.saved)).not.toEqual(gfmShape(c.md));
        } else {
          expect(gfmShape(c.saved)).toEqual(gfmShape(c.md));
        }
        expect(gfmShape(normaliseBody(c.saved))).toEqual(gfmShape(c.saved));
      });

      it("parses to a document the editor's schema accepts", () => {
        const schema = getSchema(knowledgeExtensions());
        expect(() => schema.nodeFromJSON(parseBody(c.md)).check()).not.toThrow();
        expect(() => schema.nodeFromJSON(parseBody(c.saved)).check()).not.toThrow();
      });
    });
  }
});

describe("a construct the editor cannot model stays as text", () => {
  it("keeps a block of raw HTML and inline HTML byte for byte", () => {
    const md = '<div align="center">\n<b>raw</b>\n</div>\n\nPress <kbd>Ctrl</kbd> now.';
    expect(normaliseBody(md)).toBe(md);
    const doc = parseBody(md);
    expect(doc.content?.[0]?.type).toBe("htmlBlock");
    expect(JSON.stringify(doc)).toContain('"raw":"<kbd>"');
  });

  it("keeps a <details> with no <summary> as raw HTML, not as a toggle", () => {
    const md = "<details>\nNo summary.\n</details>";
    expect(parseBody(md).content?.[0]?.type).toBe("htmlBlock");
    expect(normaliseBody(md)).toBe(md);
  });

  it("does not split an escaped <details> into a stray backslash and a block", () => {
    const doc = parseBody("\\<details> not a toggle");
    expect(doc.content?.map((n) => n.type)).toEqual(["paragraph"]);
  });

  it("draws <br> as a break, and reads it back as the same raw piece", () => {
    expect(isLineBreakHtml("<br>")).toBe(true);
    expect(isLineBreakHtml("<br />")).toBe(true);
    expect(isLineBreakHtml("<b>")).toBe(false);
  });
});

describe("a save with no edit writes nothing", () => {
  it("keeps the stored body when the editor writes what it wrote at open", () => {
    const stored = "* star bullet\n\n__strong__";
    const baseline = normaliseBody(stored);
    expect(baseline).not.toBe(stored);
    expect(bodyAfterEdit(stored, baseline, baseline)).toBe(stored);
  });

  it("takes the editor's body after a real edit", () => {
    const stored = "* star bullet";
    const baseline = normaliseBody(stored);
    expect(bodyAfterEdit(stored, baseline, `${baseline}\n\nNew line.`)).toBe(`${baseline}\n\nNew line.`);
  });

  it("an empty body opens as one empty paragraph and writes as empty", () => {
    expect(parseBody("")).toEqual({ type: "doc", content: [{ type: "paragraph" }] });
    expect(writeBody(parseBody(""))).toBe("");
  });
});

describe("the small rules", () => {
  it("draws each heading one level down, and never past h6", () => {
    expect([1, 2, 3, 4, 5, 6].map(displayHeadingLevel)).toEqual([2, 3, 4, 5, 6, 6]);
    expect(displayHeadingLevel(0)).toBe(2);
  });

  it("makes a code fence longer than any run of backticks in the code", () => {
    expect(codeFence("plain")).toBe("```");
    expect(codeFence("```js\nx\n```")).toBe("````");
    expect(codeFence("a ````` b")).toBe("``````");
  });

  it("escapes a bare pipe in a cell, and leaves an escaped one", () => {
    expect(escapeCellPipes("a | b")).toBe("a \\| b");
    expect(escapeCellPipes("a \\| b")).toBe("a \\| b");
    expect(escapeCellPipes("a \\\\| b")).toBe("a \\\\\\| b");
  });

  it("escapes a block marker at the start of a paragraph line", () => {
    expect(escapeLineStart("# not a heading")).toBe("\\# not a heading");
    expect(escapeLineStart("- not a bullet")).toBe("\\- not a bullet");
    expect(escapeLineStart("12. not a list")).toBe("12\\. not a list");
    expect(escapeLineStart("===")).toBe("\\===");
    expect(escapeLineStart("```")).toBe("\\```");
    expect(escapeLineStart("    four spaces")).toBe("four spaces");
    expect(escapeLineStart("#hashtag")).toBe("#hashtag");
    expect(escapeLineStart("-dash")).toBe("-dash");
  });

  it("escapes a summary so it cannot end its own tag", () => {
    expect(escapeSummary("5 < 6 & </summary>")).toBe("5 < 6 & &lt;/summary>");
    expect(escapeSummary("a &copy; b")).toBe("a &amp;copy; b");
    expect(unescapeSummary(escapeSummary("a < b & c > d"))).toBe("a < b & c > d");
  });

  it("puts a destination with a space or a bracket in <…>", () => {
    expect(markdownDestination("https://x.example/a.png")).toBe("https://x.example/a.png");
    expect(markdownDestination("/files/a b.png")).toBe("</files/a b.png>");
    expect(normaliseBody("![a](</files/a b.png>)")).toBe("![a](</files/a b.png>)");
  });

  it("wraps an inline node at the block level in a paragraph", () => {
    const doc = wrapInlineRuns({ type: "doc", content: [{ type: "image", attrs: { src: "/a.png" } }] });
    expect(doc.content?.[0]?.type).toBe("paragraph");
  });
});

describe("the rules of the fix round", () => {
  it("sizes a code span's fence to the backticks inside it", () => {
    expect(codeSpanFence("plain")).toEqual({ fence: "`", pad: false });
    expect(codeSpanFence("a`b")).toEqual({ fence: "``", pad: false });
    expect(codeSpanFence("``")).toEqual({ fence: "```", pad: true });
    expect(normaliseBody("`` a`b ``")).toBe("``a`b``");
  });

  it("decodes each entity, and leaves an unknown name as text", () => {
    expect(decodeEntities("A&nbsp;B &copy; &#169; &#x2713; &nosuchname;")).toBe("A\u00a0B \u00a9 \u00a9 \u2713 &nosuchname;");
    const doc = parseBody("a &mdash; b");
    expect(JSON.stringify(doc)).toContain("a \u2014 b");
    expect(JSON.stringify(parseBody("`&copy;`"))).toContain("&copy;");
  });

  it("escapes a quote inside a title", () => {
    expect(markdownTitle('say "hi"')).toBe(' "say \\"hi\\""');
    expect(markdownTitle(null)).toBe("");
  });

  it("gives the second of two lists of one family the next marker", () => {
    const list = (type: string, attrs: Record<string, unknown> = {}) => ({
      type,
      attrs,
      content: [{ type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "x" }] }] }],
    });
    const doc = separateAdjacentLists({ type: "doc", content: [list("bulletList"), list("bulletList"), list("orderedList"), list("orderedList")] });
    expect(doc.content?.map((n) => n.attrs?.bullet ?? n.attrs?.delimiter)).toEqual(["-", "*", ".", ")"]);
    expect(writeBody(doc)).toBe("- x\n\n* x\n\n1. x\n\n1) x");
  });

  it("writes a list loose when an item holds a second paragraph", () => {
    const item = (blocks: number) => ({
      type: "listItem",
      content: Array.from({ length: blocks }, () => ({ type: "paragraph", content: [{ type: "text", text: "p" }] })),
    });
    expect(isLoose({ type: "bulletList", content: [item(1), item(1)] })).toBe(false);
    expect(isLoose({ type: "bulletList", content: [item(2), item(1)] })).toBe(true);
    expect(isLoose({ type: "bulletList", attrs: { loose: true }, content: [item(1)] })).toBe(true);
  });

  it("indents a child by the width of its own marker", () => {
    expect(normaliseBody("9. nine\n10. ten\n    - under ten")).toBe("9. nine\n10. ten\n    - under ten");
    expect(normaliseBody("1. one\n   - under one")).toBe("1. one\n   - under one");
  });
});

describe("the editor binds no Mod-k", () => {
  // The shell owns ⌘K (D87). A link control on Mod-k would take the command
  // bar away from a member who writes a page.
  it("no extension of the list has a Mod-k shortcut", () => {
    const extensions = resolveExtensions(knowledgeExtensions({ placeholder: "x" }));
    const schema = getSchemaByResolvedExtensions(extensions);
    const keys: string[] = [];
    for (const ext of extensions) {
      const shortcuts = getExtensionField<(() => Record<string, unknown>) | undefined>(ext, "addKeyboardShortcuts", {
        name: ext.name,
        options: ext.options,
        storage: ext.storage,
        editor: {},
        type: getSchemaTypeByName(ext.name, schema),
      });
      if (shortcuts) keys.push(...Object.keys(shortcuts()));
    }
    expect(keys.length).toBeGreaterThan(10);
    expect(keys.filter((k) => /(?:^|-)k$/i.test(k) && /mod|meta|ctrl|cmd/i.test(k))).toEqual([]);
  });
});
