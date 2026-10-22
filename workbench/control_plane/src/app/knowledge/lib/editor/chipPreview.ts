// ── The hover preview of a page chip (WS-61 KB-2k2) ─────────────────────────
//
// Pure. The card shows the first 200 characters of the page as plain text.
// `previewText` strips the Markdown and cuts on a word. The timing constants
// are here so the component and the e2e spec read the same numbers.

/** How long a hover waits before the card opens. */
export const PREVIEW_DELAY_MS = 400;

/** How many characters of the page the card shows. */
export const PREVIEW_CHARS = 200;

/** What the card says for a page the reader cannot open. No title shows. */
export const PREVIEW_REFUSED = "You cannot open this page";

/** What the card says for a page with no text. */
export const PREVIEW_EMPTY = "This page is empty.";

/** Markdown to plain text: no marks, no links, no code fences, one line. */
export function plainText(md: string | null | undefined): string {
  let t = (md ?? "").replace(/\r\n?/g, "\n");
  t = t.replace(/^---\n[\s\S]*?\n---\n/, ""); // front matter
  t = t.replace(/^[ \t]*(```|~~~)[^\n]*$/gm, ""); // fence lines keep their code as text
  t = t.replace(/<!--[\s\S]*?-->/g, " ");
  t = t.replace(/<[^>\n]+>/g, " "); // raw HTML tags
  t = t.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1"); // image: its alt
  t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1"); // link: its text
  t = t.replace(/\[\[([^\]]*)\]\]/g, "$1");
  t = t.replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, ""); // headings
  t = t.replace(/^[ \t]*>+[ \t]?/gm, ""); // quotes
  t = t.replace(/^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?/gm, ""); // list and task marks
  t = t.replace(/^[ \t]*(?:[-*_]{3,}|\|?[ \t:|-]*---[ \t:|-]*)$/gm, ""); // rules, table rules
  t = t.replace(/\|/g, " "); // table cells
  t = t.replace(/(\*\*|__|~~|[*_`])/g, ""); // emphasis, strike, code
  t = t.replace(/\\([\\`*_{}\[\]()#+\-.!|>])/g, "$1"); // escapes
  return t.replace(/\s+/g, " ").trim();
}

/**
 * The first `max` characters of the page text, cut on a word. A cut text ends
 * with an ellipsis. A text that fits shows whole.
 */
export function previewText(md: string | null | undefined, max: number = PREVIEW_CHARS): string {
  const text = plainText(md);
  if (text.length <= max) return text;
  let cut = text.slice(0, max);
  // The next character decides: a space there means the cut is on a word end.
  if (!/\s/.test(text[max])) {
    const space = cut.lastIndexOf(" ");
    if (space > 0) cut = cut.slice(0, space);
  }
  return `${cut.trimEnd().replace(/[,;:.\-–—]+$/, "")}…`;
}
