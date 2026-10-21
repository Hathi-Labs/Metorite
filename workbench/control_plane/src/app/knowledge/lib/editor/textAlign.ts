// ── Text alignment (WS-61 KB-2p, §21.7) ─────────────────────────────────────
//
// Pure. A paragraph or a heading may be centred, right-aligned or justified.
// Left is the default and writes plain Markdown. The stored form is one line
// of HTML: `<p data-align="center">text</p>`, and `<h2 data-align="right">
// text</h2>` for a heading, with the level of the tag as its stored level.
// The text inside is inline Markdown, and the editor reads it as such.
//
// A table cell of a GFM table cannot hold the form, so `htmlTable.ts` writes
// a table with an aligned block as an HTML table instead.

import { Extension, type AnyExtension, type JSONContent, type MarkdownRendererHelpers, type RenderContext } from "@tiptap/core";

import { blockStart, findClose } from "./htmlFragment";

/** The alignments the editor stores. Left is the default and stores none. */
export const TEXT_ALIGNS = ["center", "right", "justify"] as const;
export type TextAlign = (typeof TEXT_ALIGNS)[number];

export function isTextAlign(value: unknown): value is TextAlign {
  return typeof value === "string" && (TEXT_ALIGNS as readonly string[]).includes(value);
}

/** The node types that take an alignment. */
const ALIGNED_TYPES = ["paragraph", "heading"];

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    kbTextAlign: {
      /** Align the selected blocks. `null` is left, the default. */
      setTextAlign: (align: TextAlign | null) => ReturnType;
    };
  }
}

const OPEN = /^ {0,3}<(p|h[1-6]) data-align="(center|right|justify)">/;

/**
 * The aligned block at the start of `src`: the open tag, inline Markdown
 * with no blank line, the close tag of the same name, and the end of a line.
 */
export function readAligned(src: string): { raw: string; tag: string; align: TextAlign; inner: string } | null {
  const open = OPEN.exec(src);
  if (!open) return null;
  const blank = /\n[ \t]*(?:\n|$)/.exec(src);
  const block = blank ? src.slice(0, blank.index + 1) : src;
  const close = findClose(block, open[0].length, open[1]);
  if (close === -1) return null;
  const closeTag = `</${open[1]}>`;
  const after = /^[ \t]*(?:\n|$)/.exec(block.slice(close + closeTag.length));
  if (!after) return null;
  const inner = block.slice(open[0].length, close);
  if (!inner.trim()) return null;
  return {
    raw: block.slice(0, close + closeTag.length + after[0].length),
    tag: open[1],
    align: open[2] as TextAlign,
    inner,
  };
}

/** A block's Markdown, wrapped in its aligned tag when it has an alignment. */
export function alignedMarkdown(node: JSONContent, inner: string): string {
  const align = node.attrs?.align;
  if (!isTextAlign(align) || !inner.trim()) return inner;
  if (node.type === "heading") {
    const level = Math.min(Math.max(Number(node.attrs?.level) || 1, 1), 6);
    const text = inner.replace(/^#{1,6}[ \t]+/, "");
    // A close tag inside the text would end the block early. The text then
    // keeps its words and loses the alignment, never the other way round.
    if (/<\/h[1-6]\s*>/i.test(text)) return inner;
    return `<h${level} data-align="${align}">${text}</h${level}>`;
  }
  if (/<\/p\s*>/i.test(inner) || /\n[ \t]*\n/.test(inner)) return inner;
  return `<p data-align="${align}">${inner}</p>`;
}

type Render = (node: JSONContent, h: MarkdownRendererHelpers, ctx: RenderContext) => string;

/**
 * The paragraph or heading of the schema, with its Markdown wrapped in the
 * aligned tag. `schema.ts` passes its own two nodes through this.
 */
export function withAlign<T extends AnyExtension>(ext: T): T {
  return ext.extend({
    renderMarkdown(this: { parent?: Render | null }, node: JSONContent, h: MarkdownRendererHelpers, ctx: RenderContext) {
      return alignedMarkdown(node, this.parent?.(node, h, ctx) ?? "");
    },
  }) as T;
}

/**
 * The alignment: one attribute on paragraphs and headings, the command, and
 * the reader of the stored form. The writer is `withAlign`.
 */
export const KbTextAlign = Extension.create({
  name: "kbTextAlign",

  addGlobalAttributes() {
    return [
      {
        types: ALIGNED_TYPES,
        attributes: {
          align: {
            default: null,
            parseHTML: (el: HTMLElement) => {
              const value = el.getAttribute("data-align");
              return isTextAlign(value) ? value : null;
            },
            renderHTML: (attrs: Record<string, unknown>) =>
              isTextAlign(attrs.align) ? { "data-align": attrs.align } : {},
          },
        },
      },
    ];
  },

  addCommands() {
    return {
      setTextAlign:
        (align) =>
        ({ commands }) =>
          ALIGNED_TYPES.map((type) => commands.updateAttributes(type, { align: isTextAlign(align) ? align : null })).some(
            Boolean,
          ),
    };
  },

  markdownTokenName: "kbAligned",

  markdownTokenizer: {
    name: "kbAligned",
    level: "block",
    start: (src: string) => blockStart(src, /\n {0,3}<(?:p|h[1-6]) data-align="/),
    tokenize: (src, _tokens, lexer) => {
      const found = readAligned(src);
      if (!found) return undefined;
      return { type: "kbAligned", raw: found.raw, tag: found.tag, align: found.align, tokens: lexer.inlineTokens(found.inner) };
    },
  },

  parseMarkdown: (token, h) => {
    const content = h.parseInline(token.tokens ?? []);
    const align = isTextAlign(token.align) ? token.align : null;
    const tag = String(token.tag ?? "p");
    if (tag === "p") return h.createNode("paragraph", { align }, content);
    return h.createNode("heading", { level: Number(tag.slice(1)), align }, content);
  },
});
