// HDR-1 (`one_projects_app.md` §9e.3 rule 3) — the one plain line under a
// project's title.
//
// The stored description is Markdown, and for a big project it is an agent
// brief of some kilobytes. Agents read it through the MCP, so this file
// changes nothing that is stored. It only makes a line to draw.

/** The longest line, the "…" included. */
export const SUMMARY_MAX = 160;
/** A first sentence under this length takes the next sentence with it. */
export const SUMMARY_SHORT = 40;

export interface ProjectSummary {
  /** The plain line. Empty when the description has no paragraph to take it from. */
  line: string;
  /** True when the description holds more than the line, so "More" shows. */
  more: boolean;
}

const FENCE = /^\s*(```|~~~)/;
const HEADING = /^\s{0,3}#{1,6}(\s|$)/;
const TABLE = /^\s*\|/;
const LIST = /^\s*([-*+]|\d+[.)])\s+/;
const QUOTE = /^\s*>/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;

/** Remove the Markdown marks from one paragraph. */
function plain(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<[^>\n]+>/g, "")
    .replace(/`+([^`]*)`+/g, "$1")
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/(^|[\s(])[*_]([^*_\s][^*_]*?)[*_](?=$|[\s).,;:!?])/g, "$1$2")
    .replace(/~~(.+?)~~/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

/** Split into sentences. A sentence ends at ". ", "! " or "? " before a capital or a digit. */
function sentences(text: string): string[] {
  return text.split(/(?<=[.!?])\s+(?=[A-Z0-9"“(])/).filter(Boolean);
}

/** Cut at the end of a word, with "…", so the whole line holds at most `SUMMARY_MAX`. */
function cut(text: string): string {
  if (text.length <= SUMMARY_MAX) return text;
  const room = text.slice(0, SUMMARY_MAX - 1);
  const space = room.lastIndexOf(" ");
  const head = (space > 0 ? room.slice(0, space) : room).replace(/[\s,;:.\-–—]+$/, "");
  return `${head}…`;
}

/**
 * The summary line of a description, or `null` when the description is empty.
 *
 * The rule: skip headings, tables, lists, code blocks and quotes. Take the first
 * paragraph that is left. Drop a bold label at its start. Remove the marks.
 * Keep the first sentence, and add the next one when the first is short.
 */
export function projectSummary(description: string | null | undefined): ProjectSummary | null {
  const source = (description ?? "").replace(/\r\n?/g, "\n");
  if (!source.trim()) return null;
  const lines = source.split("\n");

  // Find the first paragraph: a run of plain lines. Remember which lines it holds.
  let inFence = false;
  let start = -1;
  let end = -1;
  for (let i = 0; i < lines.length; i += 1) {
    const text = lines[i];
    if (FENCE.test(text)) {
      inFence = !inFence;
      if (start >= 0) break;
      continue;
    }
    const plainLine =
      !inFence &&
      text.trim() !== "" &&
      !HEADING.test(text) &&
      !TABLE.test(text) &&
      !LIST.test(text) &&
      !QUOTE.test(text) &&
      !RULE.test(text);
    if (plainLine) {
      if (start < 0) start = i;
      end = i;
    } else if (start >= 0) {
      break;
    }
  }

  const otherText = lines.some((text, i) => (i < start || i > end || start < 0) && text.trim() !== "");
  if (start < 0) return { line: "", more: true };

  let paragraph = lines.slice(start, end + 1).join(" ").trim();
  // A bold label at the start ("**What this app is.**") names the paragraph, so it goes.
  const label = paragraph.match(/^(\*\*|__)([^*_]+?[.:])\1\s*/);
  if (label && paragraph.length > label[0].length) paragraph = paragraph.slice(label[0].length);
  const parts = sentences(plain(paragraph));
  if (parts.length === 0) return { line: "", more: true };

  let used = 1;
  let line = parts[0];
  if (line.length < SUMMARY_SHORT && parts.length > 1) {
    line = `${line} ${parts[1]}`;
    used = 2;
  }
  const shown = cut(line);
  return { line: shown, more: otherText || used < parts.length || shown !== line };
}
