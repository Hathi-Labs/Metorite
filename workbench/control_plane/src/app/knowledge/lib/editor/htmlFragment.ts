// ── The HTML fragments of the stored forms (WS-61 KB-2p, §21.7) ─────────────
//
// Pure. A block or a mark with no Markdown syntax stores as one HTML
// fragment with exact attributes. These helpers read and write the
// fragments for the files beside this one (`textColor.ts`, `status.ts` and
// the rest). The editor recognises ONLY the exact shapes of the table in
// §21.7. Any other HTML stays raw text, as in KB-2b.
//
// ⚠️ Nothing here hands a string to the browser's HTML parser. A fragment
// becomes a node with checked attributes, and the node draws through
// ProseMirror's own DOM calls (`htmlSinks.test.ts`).

import type { MarkdownToken, MarkdownTokenizer } from "@tiptap/core";

import { decodeEntities } from "./inline";

/**
 * Text made safe inside an HTML element, with the least change: `<` before
 * a tag and `&` before an entity. So "5 < 6 & more" stays as it was.
 */
export function escapeHtmlText(text: string): string {
  return text.replace(/&(?=#?[A-Za-z0-9]+;)/g, "&amp;").replace(/<(?=[A-Za-z/!?])/g, "&lt;");
}

/** The text of an element: each entity as its character. */
export function unescapeHtmlText(text: string): string {
  return decodeEntities(text);
}

/**
 * Plain text inside a fragment that CommonMark still reads as inline
 * Markdown (a status, a date label): each mark character gets a backslash,
 * so a reader of the body sees the same words.
 */
export function escapeInlineText(text: string): string {
  return escapeHtmlText(text).replace(/([\\`*_[\]~])/g, "\\$1");
}

/** The words of an escaped text: the backslashes off, then the entities. */
export function unescapeInlineText(text: string): string {
  return unescapeHtmlText(text.replace(/\\([!-/:-@[-`{-~])/g, "$1"));
}

/** The end of the code span that opens at `i` (a run of backticks), or -1. */
function codeSpanEnd(src: string, i: number): number {
  let n = 0;
  while (src[i + n] === "`") n++;
  const fence = "`".repeat(n);
  for (let j = src.indexOf(fence, i + n); j !== -1; j = src.indexOf(fence, j + 1)) {
    let run = 0;
    while (src[j + run] === "`") run++;
    if (run === n && src[j - 1] !== "`") return j + n;
    j += run - 1;
  }
  return -1;
}

/**
 * Where the close tag `</tag>` that pairs with an open tag ends at `from`
 * starts, or -1. A nested open tag of the same name needs its own close
 * first. A tag inside a code span is code, and does not count.
 */
export function findClose(src: string, from: number, tag: string): number {
  const open = new RegExp(`^<${tag}\\b[^<>]*>`, "i");
  const close = `</${tag}>`;
  let depth = 1;
  for (let i = from; i < src.length; i++) {
    const ch = src[i];
    if (ch === "\\") {
      i++;
      continue;
    }
    if (ch === "`") {
      const end = codeSpanEnd(src, i);
      if (end !== -1) {
        i = end - 1;
        continue;
      }
      while (src[i + 1] === "`") i++;
      continue;
    }
    if (ch !== "<") continue;
    if (src.startsWith(close, i)) {
      depth--;
      if (depth === 0) return i;
      i += close.length - 1;
      continue;
    }
    const nested = open.exec(src.slice(i));
    if (nested) {
      depth++;
      i += nested[0].length - 1;
    }
  }
  return -1;
}

/**
 * Where a block of ours may start inside `src`, for marked's paragraph cut.
 * marked hands a block `start` the source with its first character cut off,
 * so a block starts only right after a newline, and `pattern` matches that
 * newline (as `lineStartIndex` in `schema.ts` does).
 */
export function blockStart(src: string, pattern: RegExp): number {
  const m = pattern.exec(src);
  return m ? m.index + 1 : -1;
}

/** The token of one inline fragment, with its inner Markdown read. */
export interface FragmentToken extends MarkdownToken {
  attrs: Record<string, string>;
}

/**
 * The tokenizer of an inline fragment that holds Markdown: an exact open
 * tag, inline Markdown, then `</tag>`. `attrsOf` reads the attributes from
 * the open tag's match and returns `null` for a value the editor does not
 * write, so the fragment stays raw. An empty fragment stays raw too.
 */
export function inlineFragment(
  name: string,
  tag: string,
  openTag: RegExp,
  attrsOf: (m: RegExpExecArray) => Record<string, string> | null,
): MarkdownTokenizer {
  const prefix = `<${tag}`;
  return {
    name,
    level: "inline",
    start: (src: string) => src.indexOf(prefix),
    tokenize: (src, _tokens, lexer) => {
      if (!src.startsWith(prefix)) return undefined;
      const m = openTag.exec(src);
      if (!m || m.index !== 0) return undefined;
      const attrs = attrsOf(m);
      if (!attrs) return undefined;
      const close = findClose(src, m[0].length, tag);
      if (close === -1) return undefined;
      const inner = src.slice(m[0].length, close);
      if (!inner.trim()) return undefined;
      return {
        type: name,
        raw: src.slice(0, close + tag.length + 3),
        attrs,
        tokens: lexer.inlineTokens(inner),
      } as FragmentToken;
    },
  };
}
