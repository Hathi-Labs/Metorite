import { describe, expect, it } from "vitest";

import {
  SCAN_MESSAGE,
  bodySize,
  headingLevels,
  itemsToLines,
  linesToBlocks,
  pdfDocToMarkdown,
  pdfToMarkdownFromItems,
  planPdfPages,
  type PdfDocLike,
  type PdfItem,
} from "./pdf";

let y = 0;
const item = (str: string, x: number, yy: number, size = 11, width = str.length * size * 0.5): PdfItem => ({ str, x, y: yy, size, width });

describe("pdf lines", () => {
  it("groups items by y into lines, top to bottom, and left to right", () => {
    const lines = itemsToLines([item("world", 60, 700), item("second", 10, 680), item("Hello", 10, 700.2)]);
    expect(lines.map((l) => l.text)).toEqual(["Hello world", "second"]);
  });

  it("adds a space only where the gap is real", () => {
    const joined = itemsToLines([item("Hel", 10, 700, 10, 15), item("lo", 25, 700, 10, 10), item("there", 50, 700, 10, 25)]);
    expect(joined[0].text).toBe("Hello there");
  });

  it("takes the size that holds most of the characters", () => {
    const lines = itemsToLines([item("Big", 10, 700, 20), item(" and a long run of small text", 40, 700, 10)]);
    expect(lines[0].size).toBe(10);
  });

  it("drops empty items", () => {
    expect(itemsToLines([item("", 0, 700), item("   ", 0, 690)])).toEqual([]);
  });
});

describe("pdf headings", () => {
  const page = () => {
    y = 760;
    const lines = [item("Annual report", 10, y, 24)];
    for (let i = 0; i < 6; i++) lines.push(item(`Body line number ${i} of the text`, 10, (y -= 14), 11));
    lines.push(item("Scope", 10, (y -= 30), 16));
    for (let i = 0; i < 4; i++) lines.push(item(`More body text here ${i} more`, 10, (y -= 14), 11));
    lines.push(item("Detail", 10, (y -= 30), 13));
    lines.push(item("Tiny note", 10, (y -= 14), 11));
    return itemsToLines(lines);
  };

  it("finds the body size as the median character", () => {
    expect(bodySize([page()])).toBe(11);
  });

  it("ranks larger sizes into levels 1 to 3, biggest first", () => {
    const levels = headingLevels([page()], 11);
    expect([...levels.entries()].sort((a, b) => b[0] - a[0])).toEqual([[24, 1], [16, 2], [13, 3]]);
  });

  it("does not make a heading of a long line or of a size near the body", () => {
    const long = itemsToLines([item("x".repeat(200), 10, 700, 20), item("body body body body body body body body", 10, 680, 11)]);
    expect(headingLevels([long], 11).size).toBe(0);
    expect(headingLevels([itemsToLines([item("Close", 10, 700, 12), item("body body body body body body", 10, 680, 11)])], 11).size).toBe(0);
  });

  it("caps the levels at three", () => {
    const sizes = [30, 26, 22, 18];
    const lines = sizes.map((s, i) => item(`H${i}`, 10, 700 - i * 40, s));
    lines.push(item("body body body body body body body body body body", 10, 400, 11));
    const levels = headingLevels([itemsToLines(lines)], 11);
    expect(Math.max(...levels.values())).toBe(3);
    expect(levels.get(18)).toBe(3);
  });
});

describe("pdf to Markdown", () => {
  it("builds headings, paragraphs by line gap, list items, and a divider between pages", () => {
    const p1 = [
      item("Handbook", 10, 760, 24),
      item("First line of a paragraph that wraps", 10, 730),
      item("onto a second line here", 10, 717),
      item("A new paragraph after a gap", 10, 680),
      item("• apples", 10, 660),
      item("• pears", 10, 647),
      item("1", 300, 20), // a page number
    ];
    const p2 = [item("Chapter two", 10, 760, 16), item("Text on the second page of the file", 10, 730)];
    const out = pdfToMarkdownFromItems([p1, p2]);
    expect(out.title).toBe("Handbook");
    expect(out.textPages).toBe(2);
    expect(out.markdown).toBe(
      [
        "First line of a paragraph that wraps onto a second line here",
        "A new paragraph after a gap",
        "- apples\n\n- pears",
        "---",
        "## Chapter two",
        "Text on the second page of the file",
      ].join("\n\n"),
    );
  });

  it("keeps a heading that is not the first block in the body, with no title", () => {
    const out = pdfToMarkdownFromItems([[item("Body text that is long enough to be the body", 10, 700), item("Sub", 10, 650, 14)]]);
    expect(out.title).toBeNull();
    expect(out.markdown).toContain("# Sub");
  });

  it("escapes Markdown in text and a leading mark", () => {
    const out = pdfToMarkdownFromItems([[item("# not a heading <b> *x*", 10, 700)]]);
    expect(out.markdown).toBe("\\# not a heading \\<b\\> \\*x\\*");
  });

  it("gives an empty body for a page with no text items (a scan)", () => {
    const out = pdfToMarkdownFromItems([[], [item("  ", 0, 0)]]);
    expect(out).toEqual({ title: null, markdown: "", textPages: 0 });
  });

  it("splits blocks by gap and by heading change", () => {
    const lines = itemsToLines([item("Head", 10, 700, 20), item("body body body body body body", 10, 650), item("body two body two body two", 10, 636)]);
    const blocks = linesToBlocks(lines, new Map([[20, 1]]));
    expect(blocks.map((b) => [b.level, b.lines.length])).toEqual([[1, 1], [0, 2]]);
  });
});

describe("pdf reader limits", () => {
  const fakeDoc = (numPages: number, withText: boolean) => {
    const read: number[] = [];
    const doc: PdfDocLike = {
      numPages,
      getPage: async (n) => {
        read.push(n);
        return {
          getTextContent: async () => ({
            items: withText ? [{ str: `Page ${n} text`, transform: [1, 0, 0, 11, 10, 700], height: 11, width: 50 }] : [],
          }),
          cleanup: () => {},
        };
      },
    };
    return { doc, read };
  };

  it("plans 300 pages and a notice for a 301-page document", () => {
    expect(planPdfPages(301)).toEqual({ count: 300, notice: "Only the first 300 of 301 PDF pages were read." });
    expect(planPdfPages(300)).toEqual({ count: 300, notice: null });
  });

  it("reads 300 pages of a 301-page document and adds the notice", async () => {
    const { doc, read } = fakeDoc(301, true);
    const out = await pdfDocToMarkdown(doc);
    expect(read).toHaveLength(300);
    expect(read[299]).toBe(300);
    expect(out.notices).toEqual(["Only the first 300 of 301 PDF pages were read."]);
  });

  it("refuses a document with no text layer, and does not give an empty page", async () => {
    await expect(pdfDocToMarkdown(fakeDoc(3, false).doc)).rejects.toThrow(SCAN_MESSAGE);
  });
});
