// ── PowerPoint to Markdown (WS-61 KB-AI-1) ───────────────────────────────────
//
// One deck is one page. Each slide is a `## Slide N: <title>` section. The
// title comes from the title placeholder, and every other text shape gives
// bullets, indented by their level. A table gives a Markdown table. The notes
// of a slide go under a `**Notes**` line. A picture gives nothing, because
// attachments wait for KB-2c.
//
// `slideToMarkdown` is pure and works on the tree of `xml.ts`. `pptxToMarkdown`
// reads the zip, and it needs a browser, because `parseXml` does.

import { MAX_SLIDES } from "./limits";
import { escapeInline, escapeLine, toTable } from "./mdText";
import { DocumentRefusal, readParts } from "./ooxml";
import { all, kid, kids, parseXml, textOf, type XEl } from "./xml";

/** The placeholders that hold a slide number, a footer or a date. */
const FURNITURE = new Set(["sldNum", "ftr", "dt", "hdr", "sldImg"]);

/** The text of one `a:p` paragraph: its runs and fields, with a line break as a space. */
export function paragraphText(p: XEl): string {
  let out = "";
  for (const k of p.kids) {
    if (typeof k === "string") continue;
    if (k.name === "r" || k.name === "fld") out += all(k, "t").map(textOf).join("");
    else if (k.name === "br") out += " ";
  }
  return out.replace(/\s+/g, " ").trim();
}

function placeholder(sp: XEl): string | null {
  return all(sp, "ph")[0]?.attrs.type ?? (all(sp, "ph").length ? "body" : null);
}

export interface SlideRead {
  title: string | null;
  /** Bullets in order, each with a nesting level from 0. */
  bullets: Array<{ level: number; text: string }>;
  /** Each table as rows of cell text. */
  tables: string[][][];
}

/** Read one slide. */
export function readSlide(root: XEl): SlideRead {
  const out: SlideRead = { title: null, bullets: [], tables: [] };
  for (const sp of all(root, "sp")) {
    const ph = placeholder(sp);
    if (ph && FURNITURE.has(ph)) continue;
    const paras = all(sp, "p");
    if (ph === "title" || ph === "ctrTitle") {
      const text = paras.map(paragraphText).filter(Boolean).join(" ");
      if (text && out.title === null) out.title = text;
      continue;
    }
    for (const p of paras) {
      const text = paragraphText(p);
      if (!text) continue;
      const lvl = Number.parseInt(kid(p, "pPr")?.attrs.lvl ?? "0", 10);
      out.bullets.push({ level: Number.isFinite(lvl) && lvl > 0 ? Math.min(lvl, 8) : 0, text });
    }
  }
  for (const tbl of all(root, "tbl")) {
    const rows = all(tbl, "tr").map((tr) =>
      kids(tr, "tc").map((tc) => all(tc, "p").map(paragraphText).filter(Boolean).join(" ")),
    );
    if (rows.some((r) => r.some(Boolean))) out.tables.push(rows);
  }
  return out;
}

/** The text of the notes shapes: the body placeholder only. */
export function notesText(root: XEl): string[] {
  const lines: string[] = [];
  for (const sp of all(root, "sp")) {
    if (placeholder(sp) !== "body") continue;
    for (const p of all(sp, "p")) {
      const text = paragraphText(p);
      if (text) lines.push(text);
    }
  }
  return lines;
}

/** One slide as a section. `hasText` is false when it holds no text at all. */
export function slideToMarkdown(n: number, slide: SlideRead, notes: readonly string[] = []): { markdown: string; hasText: boolean } {
  const parts: string[] = [`## ${slide.title ? `Slide ${n}: ${escapeInline(slide.title)}` : `Slide ${n}`}`];
  if (slide.bullets.length) {
    parts.push(slide.bullets.map((b) => `${"  ".repeat(b.level)}- ${escapeLine(b.text)}`).join("\n"));
  }
  for (const rows of slide.tables) parts.push(toTable(rows));
  if (notes.length) parts.push(["**Notes**", ...notes.map((line) => escapeLine(line))].join("\n\n"));
  const hasText = Boolean(slide.title) || slide.bullets.length > 0 || slide.tables.length > 0 || notes.length > 0;
  return { markdown: parts.join("\n\n"), hasText };
}

const SLIDE = /^ppt\/slides\/slide(\d+)\.xml$/;
const SLIDE_RELS = /^ppt\/slides\/_rels\/slide(\d+)\.xml\.rels$/;
const NOTES = /^ppt\/notesSlides\/notesSlide\d+\.xml$/;

export interface PptxResult {
  markdown: string;
  notices: string[];
}

/** A deck as one Markdown body. Browser only. */
export async function pptxToMarkdown(data: Uint8Array): Promise<PptxResult> {
  const parts = await readParts(data, (name) => SLIDE.test(name) || SLIDE_RELS.test(name) || NOTES.test(name));
  const numbers = [...parts.keys()]
    .map((name) => SLIDE.exec(name)?.[1])
    .filter((v): v is string => v !== undefined)
    .map(Number)
    .sort((a, b) => a - b);
  if (!numbers.length) throw new DocumentRefusal("This deck has no slides.");
  const notices: string[] = [];
  const used = numbers.slice(0, MAX_SLIDES);
  if (numbers.length > MAX_SLIDES) {
    notices.push(`Only the first ${MAX_SLIDES} of ${numbers.length} slides were read.`);
  }
  const sections: string[] = [];
  let anyText = false;
  for (const [i, num] of used.entries()) {
    const slide = readSlide(parseXml(parts.get(`ppt/slides/slide${num}.xml`) as string));
    let notes: string[] = [];
    const rels = parts.get(`ppt/slides/_rels/slide${num}.xml.rels`);
    if (rels) {
      const target = all(parseXml(rels), "Relationship").find((r) => /\/notesSlide$/.test(r.attrs.Type ?? ""))?.attrs.Target;
      const notesPath = target ? `ppt/notesSlides/${target.split("/").pop()}` : "";
      const xml = notesPath ? parts.get(notesPath) : undefined;
      if (xml) notes = notesText(parseXml(xml));
    }
    const out = slideToMarkdown(i + 1, slide, notes);
    anyText ||= out.hasText;
    sections.push(out.markdown);
  }
  if (!anyText) throw new DocumentRefusal("This deck holds no text.");
  return { markdown: sections.join("\n\n"), notices };
}
