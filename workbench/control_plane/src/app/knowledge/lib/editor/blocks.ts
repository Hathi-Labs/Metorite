// ── The blocks of the `/` menu (WS-61 KB-2b, §21.3) ─────────────────────────
//
// Pure. One list: the `/` menu offers it, and the tests insert each entry.
// Each block names the act it runs on the editor. Two entries need the
// component: an image asks for its address in a dialog, and a page link
// opens the `[[` picker.

import type { Editor, Range } from "@tiptap/core";

import { CALLOUT_TONES, type CalloutTone } from "./schema";

export type BlockKind = "insert" | "image" | "pageLink";

export interface BlockItem {
  id: string;
  label: string;
  /** Other words a member may type for it. */
  keywords: string;
  /** A Lucide name, drawn through `<Icon>`. */
  icon: string;
  kind: BlockKind;
  /** The act, for an `insert` block. The range is the typed `/query`. */
  run?: (editor: Editor, range: Range) => void;
}

/** The words and the glyph of each callout tone. */
export const CALLOUT_LOOK: Record<CalloutTone, { label: string; icon: string }> = {
  note: { label: "Note", icon: "Info" },
  tip: { label: "Tip", icon: "Lightbulb" },
  important: { label: "Important", icon: "MessageSquareWarning" },
  warning: { label: "Warning", icon: "TriangleAlert" },
  caution: { label: "Caution", icon: "OctagonAlert" },
};

/**
 * Delete the typed `/query`, then act. A list only wraps a paragraph, so the
 * list blocks turn a heading into a paragraph first.
 */
const at = (editor: Editor, range: Range) => editor.chain().focus().deleteRange(range);

export const BLOCKS: readonly BlockItem[] = [
  { id: "text", label: "Text", keywords: "paragraph plain body", icon: "Pilcrow", kind: "insert",
    run: (e, r) => at(e, r).setParagraph().run() },
  { id: "h1", label: "Heading 1", keywords: "title h1 #", icon: "Heading1", kind: "insert",
    run: (e, r) => at(e, r).setHeading({ level: 1 }).run() },
  { id: "h2", label: "Heading 2", keywords: "subtitle h2 ##", icon: "Heading2", kind: "insert",
    run: (e, r) => at(e, r).setHeading({ level: 2 }).run() },
  { id: "h3", label: "Heading 3", keywords: "h3 ###", icon: "Heading3", kind: "insert",
    run: (e, r) => at(e, r).setHeading({ level: 3 }).run() },
  { id: "bullet", label: "Bulleted list", keywords: "ul unordered points -", icon: "List", kind: "insert",
    run: (e, r) => at(e, r).setParagraph().toggleBulletList().run() },
  { id: "ordered", label: "Numbered list", keywords: "ol ordered steps 1.", icon: "ListOrdered", kind: "insert",
    run: (e, r) => at(e, r).setParagraph().toggleOrderedList().run() },
  { id: "task", label: "Task list", keywords: "todo checkbox checklist [ ]", icon: "ListTodo", kind: "insert",
    run: (e, r) => at(e, r).setParagraph().toggleTaskList().run() },
  { id: "quote", label: "Quote", keywords: "blockquote citation >", icon: "Quote", kind: "insert",
    run: (e, r) => at(e, r).setBlockquote().run() },
  ...CALLOUT_TONES.map(
    (tone): BlockItem => ({
      id: `callout-${tone}`,
      label: `${CALLOUT_LOOK[tone].label} callout`,
      keywords: `callout alert admonition panel ${tone}`,
      icon: CALLOUT_LOOK[tone].icon,
      kind: "insert",
      run: (e, r) => at(e, r).setCallout(tone).run(),
    }),
  ),
  { id: "toggle", label: "Toggle", keywords: "details collapse fold summary expand", icon: "ListCollapse", kind: "insert",
    run: (e, r) => at(e, r).insertToggle().run() },
  { id: "code", label: "Code block", keywords: "pre snippet program ```", icon: "SquareCode", kind: "insert",
    run: (e, r) => at(e, r).setCodeBlock().run() },
  { id: "table", label: "Table", keywords: "grid rows columns", icon: "Table", kind: "insert",
    run: (e, r) => at(e, r).insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
  { id: "divider", label: "Divider", keywords: "hr rule line separator ---", icon: "Minus", kind: "insert",
    run: (e, r) => at(e, r).setHorizontalRule().run() },
  // KB-2i (§21.7): a footnote mark at the caret, and its note at the end.
  { id: "footnote", label: "Footnote", keywords: "reference citation note [^", icon: "Superscript", kind: "insert",
    run: (e, r) => at(e, r).addFootnote().run() },
  { id: "image", label: "Image", keywords: "picture photo url img", icon: "Image", kind: "image" },
  { id: "page", label: "Link to a page", keywords: "page wiki link [[", icon: "FileText", kind: "pageLink" },
];

/** True when each letter of `needle` comes in order in `hay`. */
function isSubsequence(needle: string, hay: string): boolean {
  let i = 0;
  for (const ch of hay) if (ch === needle[i]) i++;
  return i === needle.length;
}

/** How well `query` names `item`: 0 is no match. */
export function blockScore(item: BlockItem, query: string): number {
  const q = query.trim().toLowerCase();
  if (!q) return 1;
  const label = item.label.toLowerCase();
  if (label.startsWith(q)) return 4;
  const words = `${label} ${item.keywords.toLowerCase()}`.split(/\s+/);
  if (words.some((w) => w.startsWith(q))) return 3;
  if (label.includes(q) || item.keywords.toLowerCase().includes(q)) return 2;
  if (q.length >= 2 && isSubsequence(q, label.replace(/\s+/g, ""))) return 1;
  return 0;
}

/** The blocks that match the typed words, best first, in list order on a tie. */
export function matchBlocks(query: string, items: readonly BlockItem[] = BLOCKS): BlockItem[] {
  return items
    .map((item, index) => ({ item, index, score: blockScore(item, query) }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((x) => x.item);
}

// ── The languages of a code block ───────────────────────────────────────────

/** The languages the picker offers. `""` is plain text. */
export const CODE_LANGUAGES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "Plain text" },
  { value: "bash", label: "Bash" },
  { value: "css", label: "CSS" },
  { value: "diff", label: "Diff" },
  { value: "go", label: "Go" },
  { value: "html", label: "HTML" },
  { value: "java", label: "Java" },
  { value: "javascript", label: "JavaScript" },
  { value: "json", label: "JSON" },
  { value: "markdown", label: "Markdown" },
  { value: "python", label: "Python" },
  { value: "rust", label: "Rust" },
  { value: "sql", label: "SQL" },
  { value: "typescript", label: "TypeScript" },
  { value: "yaml", label: "YAML" },
];

/**
 * The picker's options for a block whose language is `current`. A language
 * the list does not know stays in it, so a pick never drops what the body
 * says (`ts`, `py` and `sh` are common spellings).
 */
export function languageOptions(current: string | null | undefined): Array<{ value: string; label: string }> {
  const lang = (current ?? "").trim();
  if (!lang || CODE_LANGUAGES.some((l) => l.value === lang)) return [...CODE_LANGUAGES];
  return [{ value: lang, label: lang }, ...CODE_LANGUAGES];
}

/** The words a code block shows for its language. */
export function languageLabel(current: string | null | undefined): string {
  const lang = (current ?? "").trim();
  return CODE_LANGUAGES.find((l) => l.value === lang)?.label ?? lang;
}
