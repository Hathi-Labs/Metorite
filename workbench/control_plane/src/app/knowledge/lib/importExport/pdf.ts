// ── PDF to Markdown (WS-61 KB-AI-1) ──────────────────────────────────────────
//
// Reads the TEXT LAYER of a PDF with pdf.js, page by page, and builds light
// structure from it. The text items of a page become lines (by their y
// position) and the lines become blocks (by their gap). A line is a heading
// when its font is clearly larger than the body font, and the sizes rank into
// at most three levels. A thin `---` between two pages keeps the source page
// traceable. A PDF with no text layer is a scan. It gives no page, and the
// report says so, because OCR is not available yet.
//
// The grouping and heading rules are pure and work on plain items. `pdfToMarkdown`
// needs a browser. It loads pdf.js through `react-pdf`, as the artifact viewer
// does, and uses the same worker file (`/pdf.worker.min.mjs`). It reads 300
// pages at most.

import { MAX_PDF_PAGES } from "./limits";
import { escapeLine } from "./mdText";
import { DocumentRefusal } from "./ooxml";

/** One text item of a page, in PDF space (y grows upward). */
export interface PdfItem {
  str: string;
  x: number;
  y: number;
  /** The font height. */
  size: number;
  /** The drawn width, or 0 when unknown. */
  width: number;
}

export interface PdfLine {
  text: string;
  x: number;
  y: number;
  size: number;
}

/** The lines of one page, top to bottom. Pure. */
export function itemsToLines(items: readonly PdfItem[]): PdfLine[] {
  const sorted = items.filter((i) => i.str.length > 0).sort((a, b) => b.y - a.y || a.x - b.x);
  const groups: PdfItem[][] = [];
  let anchor = Number.NaN;
  for (const item of sorted) {
    const group = groups[groups.length - 1];
    const tol = Math.max(item.size, 1) * 0.5;
    if (group && Math.abs(anchor - item.y) <= tol) group.push(item);
    else {
      groups.push([item]);
      anchor = item.y;
    }
  }
  const lines: PdfLine[] = [];
  for (const group of groups) {
    group.sort((a, b) => a.x - b.x);
    let text = "";
    let prev: PdfItem | null = null;
    const chars = new Map<number, number>();
    for (const item of group) {
      if (prev) {
        const gap = item.x - (prev.x + prev.width);
        const touches = /\s$/.test(text) || /^\s/.test(item.str);
        if (!touches && gap > Math.max(prev.size, item.size) * 0.15) text += " ";
      }
      text += item.str;
      const key = Math.round(item.size * 2) / 2;
      chars.set(key, (chars.get(key) ?? 0) + item.str.trim().length);
      prev = item;
    }
    const clean = text.replace(/\s+/g, " ").trim();
    if (!clean) continue;
    // The size of a line is the size that holds most of its characters.
    let size = group[0].size;
    let best = -1;
    for (const [key, n] of chars) {
      if (n > best) {
        best = n;
        size = key;
      }
    }
    lines.push({ text: clean, x: group[0].x, y: group[0].y, size });
  }
  return lines;
}

/** The size that holds the middle character of all the text. */
export function bodySize(pages: readonly (readonly PdfLine[])[]): number {
  const weights = new Map<number, number>();
  let total = 0;
  for (const page of pages) {
    for (const line of page) {
      const key = Math.round(line.size * 2) / 2;
      weights.set(key, (weights.get(key) ?? 0) + line.text.length);
      total += line.text.length;
    }
  }
  let acc = 0;
  for (const key of [...weights.keys()].sort((a, b) => a - b)) {
    acc += weights.get(key) as number;
    if (acc * 2 >= total) return key;
  }
  return 0;
}

/** A heading must be this much larger than the body font, and short. */
export const HEADING_RATIO = 1.15;
export const HEADING_MAX_CHARS = 150;

/** The heading level (1 to 3) of each large size, by rank. A body size is absent. */
export function headingLevels(pages: readonly (readonly PdfLine[])[], body: number): Map<number, 1 | 2 | 3> {
  const sizes = new Set<number>();
  for (const page of pages) {
    for (const line of page) {
      const key = Math.round(line.size * 2) / 2;
      if (body > 0 && key >= body * HEADING_RATIO && line.text.length <= HEADING_MAX_CHARS) sizes.add(key);
    }
  }
  const ranked = [...sizes].sort((a, b) => b - a);
  return new Map(ranked.map((size, i) => [size, (Math.min(i, 2) + 1) as 1 | 2 | 3]));
}

const BULLET = /^([•●▪◦‣–—*-]|\d{1,3}[.)])\s+/;

interface Block {
  level: 0 | 1 | 2 | 3;
  list: boolean;
  lines: string[];
}

/** The blocks of one page: headings, paragraphs and list items. Pure. */
export function linesToBlocks(lines: readonly PdfLine[], levels: ReadonlyMap<number, 1 | 2 | 3>): Block[] {
  const blocks: Block[] = [];
  let prev: PdfLine | null = null;
  for (const line of lines) {
    const level = levels.get(Math.round(line.size * 2) / 2) ?? 0;
    const bullet = level === 0 && BULLET.test(line.text);
    const last = blocks[blocks.length - 1];
    const gap = prev ? prev.y - line.y : 0;
    const apart = gap > line.size * (level ? 2 : 1.7);
    if (last && !bullet && level === last.level && !apart) {
      last.lines.push(line.text);
    } else {
      blocks.push({ level, list: bullet, lines: [line.text] });
    }
    prev = line;
  }
  return blocks;
}

function blockMarkdown(block: Block): string {
  const text = block.lines.join(" ");
  if (block.level) return `${"#".repeat(block.level)} ${escapeLine(text)}`;
  if (block.list) return `- ${escapeLine(text.replace(BULLET, ""))}`;
  return escapeLine(text);
}

/** A line that holds only a page number: digits, or `Page 3`. */
function isPageNumber(text: string): boolean {
  return /^(page\s+)?\d{1,4}(\s*(of|\/)\s*\d{1,4})?$/i.test(text.trim());
}

export interface PdfMarkdown {
  /** The text of a level-1 heading that opens the first page with text, or `null`. */
  title: string | null;
  markdown: string;
  /** The pages that held text. */
  textPages: number;
}

/** Pages of items as one Markdown body. Pure. */
export function pdfToMarkdownFromItems(pages: readonly (readonly PdfItem[])[]): PdfMarkdown {
  const perPage = pages.map((items) => {
    const lines = itemsToLines(items);
    // The first or last line that is only a page number is furniture.
    if (lines.length > 1 && isPageNumber(lines[lines.length - 1].text)) lines.pop();
    if (lines.length > 1 && isPageNumber(lines[0].text)) lines.shift();
    return lines;
  });
  const body = bodySize(perPage);
  const levels = headingLevels(perPage, body);
  const rendered = perPage.map((lines) => linesToBlocks(lines, levels)).filter((blocks) => blocks.length > 0);
  let title: string | null = null;
  const first = rendered[0]?.[0];
  if (first && first.level === 1) {
    title = first.lines.join(" ");
    rendered[0] = rendered[0].slice(1);
  }
  const textPages = rendered.length;
  const text = rendered
    .filter((blocks) => blocks.length > 0)
    .map((blocks) => blocks.map(blockMarkdown).join("\n\n"))
    .filter(Boolean)
    .join("\n\n---\n\n");
  return { title, markdown: text, textPages };
}

export interface PdfResult extends PdfMarkdown {
  notices: string[];
}

/** The refusal for a PDF that has no text. */
export const SCAN_MESSAGE = "This PDF has no text layer (a scan). OCR is not available yet.";

/** How many pages to read, and the notice when the cap cuts the document. Pure. */
export function planPdfPages(numPages: number): { count: number; notice: string | null } {
  if (numPages <= MAX_PDF_PAGES) return { count: numPages, notice: null };
  return { count: MAX_PDF_PAGES, notice: `Only the first ${MAX_PDF_PAGES} of ${numPages} PDF pages were read.` };
}

/** Read a PDF file as a Markdown body. Browser only. */
export async function pdfToMarkdown(data: Uint8Array): Promise<PdfResult> {
  const { pdfjs } = await import("react-pdf");
  // The same worker file as the artifact viewer (`ArtifactViewerModal.tsx`).
  pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
  const task = pdfjs.getDocument({ data: data.slice(), isEvalSupported: false, enableXfa: false });
  let doc;
  try {
    doc = await task.promise;
  } catch (err) {
    const name = (err as { name?: string } | null)?.name ?? "";
    if (name === "PasswordException") throw new DocumentRefusal("This PDF is protected by a password.");
    throw new DocumentRefusal("This PDF cannot be read. It may be damaged.");
  }
  try {
    return await pdfDocToMarkdown(doc);
  } finally {
    await doc.destroy();
  }
}

/** The part of a pdf.js document that the reader uses. A test passes a stand-in. */
export interface PdfDocLike {
  numPages: number;
  getPage(n: number): Promise<{
    getTextContent(): Promise<{ items: ReadonlyArray<unknown> }>;
    cleanup(): void;
  }>;
}

/** Read the pages of an open document as Markdown. Refuses a scan. */
export async function pdfDocToMarkdown(doc: PdfDocLike): Promise<PdfResult> {
  const notices: string[] = [];
  const plan = planPdfPages(doc.numPages);
  const count = plan.count;
  if (plan.notice) notices.push(plan.notice);
  const pages: PdfItem[][] = [];
  for (let n = 1; n <= count; n++) {
    const page = await doc.getPage(n);
    const content = await page.getTextContent();
    const items: PdfItem[] = [];
    for (const raw of content.items) {
      const item = raw as { str?: string; transform: number[]; height: number; width: number };
      if (typeof item.str !== "string") continue;
      const t = item.transform;
      const size = item.height > 0 ? item.height : Math.hypot(t[2], t[3]);
      items.push({ str: item.str, x: t[4], y: t[5], size, width: item.width });
    }
    pages.push(items);
    page.cleanup();
  }
  const out = pdfToMarkdownFromItems(pages);
  if (!out.markdown.trim() && !out.title) throw new DocumentRefusal(SCAN_MESSAGE);
  return { ...out, notices };
}
