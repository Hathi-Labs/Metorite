// ── Highlight (WS-61 KB-2p, §21.7) ──────────────────────────────────────────
//
// Pure. Stored as `<mark data-color="yellow">text</mark>`, or with a hex
// value (`<mark data-color="#fde68a">`) that another tool wrote. A `<mark>`
// with no colour is not a stored form, and it stays raw text.
//
// Docmost's highlight puts the stored value in a `style` as it is. Here only
// a hex that `isHexColor` accepts reaches a style, and a name draws through
// its token in `globals.css` (`.kb-highlight`).

import { Mark, type JSONContent } from "@tiptap/core";

import { hexStyle, isHighlightColor } from "./colors";
import { inlineFragment } from "./htmlFragment";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    highlight: {
      /** Highlight the selected text. `null` takes the highlight off. */
      setHighlight: (color: string | null) => ReturnType;
    };
  }
}

const OPEN = /^<mark data-color="([^"<>]*)">/;

export const Highlight = Mark.create({
  name: "highlight",

  addAttributes() {
    return {
      color: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute("data-color"),
        renderHTML: (attrs: Record<string, unknown>) => {
          if (!isHighlightColor(attrs.color)) return {};
          const style = hexStyle("background-color", attrs.color);
          return { "data-color": attrs.color, ...(style ? { style } : {}) };
        },
      },
    };
  },

  parseHTML() {
    return [
      {
        tag: "mark[data-color]",
        getAttrs: (el: HTMLElement) => (isHighlightColor(el.getAttribute("data-color")) ? null : false),
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ["mark", { ...HTMLAttributes, class: "kb-highlight" }, 0];
  },

  addCommands() {
    return {
      setHighlight:
        (color) =>
        ({ commands }) =>
          color && isHighlightColor(color) ? commands.setMark(this.name, { color }) : commands.unsetMark(this.name),
    };
  },

  markdownTokenizer: inlineFragment("highlight", "mark", OPEN, (m) =>
    isHighlightColor(m[1]) ? { color: m[1] } : null,
  ),

  parseMarkdown: (token, h) => h.applyMark("highlight", h.parseInline(token.tokens ?? []), token.attrs),

  renderMarkdown: (node: JSONContent, h) => {
    const color = node.attrs?.color;
    const inner = h.renderChildren(node);
    return isHighlightColor(color) ? `<mark data-color="${color}">${inner}</mark>` : inner;
  },
});
