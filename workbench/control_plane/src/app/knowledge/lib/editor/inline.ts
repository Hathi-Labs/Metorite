// ── Inline Markdown of the block editor (WS-61 KB-2b fix round) ───────────
//
// Pure. The rules that read and write the text inside one block: entities,
// links (with an image or text inside), code spans, footnote marks, and the
// escapes at the start of a line.
//
// Each rule exists because the stock `@tiptap/markdown` round trip lost
// content here. The golden set in `tests/fixtures/kb_editor_golden.json`
// holds each case, and the golden test reads every saved body with a
// CommonMark and GFM parser too (`editorMarkdown.test.ts`).

import type { JSONContent, MarkdownToken, MarkdownTokenizer } from "@tiptap/core";
import { decodeNamedCharacterReference } from "decode-named-character-reference";

// ── Entities ────────────────────────────────────────────────────────────────

const ENTITY = /^&(?:#[xX]([0-9a-fA-F]{1,6})|#([0-9]{1,7})|([A-Za-z][A-Za-z0-9]{1,31}));/;

/** The character of one entity, or `null` when it is not a real entity. */
export function decodeEntity(raw: string): string | null {
  const m = ENTITY.exec(raw);
  if (!m || m[0] !== raw) return null;
  if (m[1] || m[2]) {
    const code = m[1] ? parseInt(m[1], 16) : parseInt(m[2], 10);
    // CommonMark reads 0 and an invalid code point as U+FFFD.
    if (code === 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return "�";
    return String.fromCodePoint(code);
  }
  const named = decodeNamedCharacterReference(m[3]);
  return named === false ? null : named;
}

/** Each entity in `text` as its character. An unknown name stays as text. */
export function decodeEntities(text: string): string {
  return text.replace(/&(?:#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[A-Za-z][A-Za-z0-9]{1,31});/g, (raw) => decodeEntity(raw) ?? raw);
}

/**
 * An entity in running text, as its own token. marked keeps `&nbsp;` and
 * `&#169;` in the text, and the stock parser decodes only `&lt;`, `&gt;`,
 * `&quot;` and `&amp;`. So `&copy;` showed as text, and a save wrote
 * `&amp;copy;`. A code span is read before this, so code keeps its entities.
 */
export const entityTokenizer: MarkdownTokenizer = {
  name: "kbEntity",
  level: "inline",
  start: (src: string) => src.indexOf("&"),
  tokenize: (src: string) => {
    const m = ENTITY.exec(src);
    if (!m) return undefined;
    const char = decodeEntity(m[0]);
    return char === null ? undefined : { type: "kbEntity", raw: m[0], text: char };
  },
};

// ── Footnotes ───────────────────────────────────────────────────────────────

/** A footnote mark `[^label]` in text. GFM draws it, and marked does not. */
export const FOOTNOTE_REF = /^\[\^[^\]\s]{1,100}\](?!:)/;

/** A footnote definition and its indented lines, at the start of a block. */
export const FOOTNOTE_DEF = /^\[\^[^\]\s]{1,100}\]:[^\n]*(?:\n(?:[ \t]*\n)*(?: {4}|\t)[^\n]*)*/;

// ── Escapes at the start of a line ──────────────────────────────────────────

/**
 * One line of a paragraph, made safe at its start. Text that begins with a
 * block marker (`# `, `- `, `1. `, a rule, a setext line or a fence) would
 * read back as a heading, a list, a rule or code. So the marker gets its
 * backslash, and leading spaces go, because four of them make code.
 */
export function escapeLineStart(line: string): string {
  const s = line.replace(/^[ \t]+/, "");
  if (/^#{1,6}(?=[ \t]|$)/.test(s)) return `\\${s}`;
  if (/^[-+](?=[ \t]|$)/.test(s)) return `\\${s}`;
  if (/^(?:=+|-+)[ \t]*$/.test(s)) return `\\${s}`;
  if (/^(?:`{3,}|~{3,})/.test(s)) return `\\${s}`;
  const ordered = /^(\d{1,9})([.)])(?=[ \t]|$)/.exec(s);
  if (ordered) return `${ordered[1]}\\${s.slice(ordered[1].length)}`;
  return s;
}

// ── Links and images ────────────────────────────────────────────────────────

/** True when each `(` in `s` closes, so a bare destination keeps them. */
function parensBalance(s: string): boolean {
  let depth = 0;
  for (const ch of s) {
    if (ch === "(") depth++;
    else if (ch === ")" && --depth < 0) return false;
  }
  return depth === 0;
}

/**
 * The destination of a link or an image. It goes in `<…>` when a bare one
 * would end early: a space, an angle bracket, an unbalanced bracket, or
 * nothing at all.
 */
export function markdownDestination(src: string): string {
  if (src === "" || /[\s<>]/.test(src) || !parensBalance(src)) {
    return `<${src.replace(/[<>\n]/g, (c) => encodeURIComponent(c))}>`;
  }
  return src;
}

/** A link or image title in quotes, with its own quotes escaped. */
export function markdownTitle(title: unknown): string {
  if (typeof title !== "string" || title === "") return "";
  return ` "${title.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

type LinkAttrs = { href: string; title: string | null };

function linkOf(node: JSONContent): LinkAttrs | null {
  const mark = (node.marks ?? []).find((m) => m.type === "link");
  if (!mark) return null;
  return { href: String(mark.attrs?.href ?? ""), title: (mark.attrs?.title as string | null) ?? null };
}

const sameLink = (a: LinkAttrs | null, b: LinkAttrs | null) =>
  Boolean(a && b && a.href === b.href && (a.title ?? null) === (b.title ?? null));

const withoutLink = (node: JSONContent): JSONContent => ({
  ...node,
  marks: (node.marks ?? []).filter((m) => m.type !== "link"),
});

/**
 * The marks of a link, put on every inline node it holds: text, an image,
 * raw inline text. The stock rule marked only text, so a linked image lost
 * its link, and `[see ![a](i) here](u)` came apart in three.
 */
export function linkContent(content: JSONContent[], token: MarkdownToken): JSONContent[] {
  const link = { type: "link", attrs: { href: token.href ?? "", title: token.title || null } };
  return content.map((n) => ({ ...n, marks: [...(n.marks ?? []), link] }));
}

type Renderer = { renderChildren: (nodes: JSONContent | JSONContent[], separator?: string) => string };

/**
 * The inline content of one block as Markdown. Each run of nodes under one
 * link writes as one `[…](…)`, with the link taken off its nodes, so an
 * image or a mark inside a link stays inside it. The rest writes through
 * the stock mark rules.
 */
export function renderInline(nodes: JSONContent[], h: Renderer): string {
  const out: string[] = [];
  let i = 0;
  while (i < nodes.length) {
    const link = linkOf(nodes[i]);
    let j = i + 1;
    if (!link) {
      while (j < nodes.length && !linkOf(nodes[j])) j++;
      out.push(h.renderChildren(nodes.slice(i, j)));
    } else {
      while (j < nodes.length && sameLink(linkOf(nodes[j]), link)) j++;
      const inner = h.renderChildren(nodes.slice(i, j).map(withoutLink));
      out.push(`[${inner}](${markdownDestination(link.href)}${markdownTitle(link.title)})`);
    }
    i = j;
  }
  return out.join("");
}

// ── Code spans ──────────────────────────────────────────────────────────────

/**
 * The backticks around a code span: one more than the longest run inside,
 * and a space inside each end when the code starts or ends with a backtick
 * or a space. `` a`b `` then stays one span.
 */
export function codeSpanFence(code: string): { fence: string; pad: boolean } {
  let longest = 0;
  for (const run of code.match(/`+/g) ?? []) longest = Math.max(longest, run.length);
  const fence = "`".repeat(longest + 1);
  const pad = /^[` ]|[` ]$/.test(code) && code.trim() !== "";
  return { fence, pad };
}

/**
 * Put the fence of each code span on its mark, before a write. The Code
 * mark reads it in `renderMarkdown`, because a mark writes its opening and
 * closing without the text between them.
 */
export function prepareCodeSpans(node: JSONContent): JSONContent {
  const marks = node.marks?.map((m) =>
    m.type === "code" && node.type === "text" ? { ...m, attrs: { ...(m.attrs ?? {}), ...codeSpanFence(node.text ?? "") } } : m,
  );
  const content = node.content?.map(prepareCodeSpans);
  return { ...node, ...(marks ? { marks } : {}), ...(content ? { content } : {}) };
}
