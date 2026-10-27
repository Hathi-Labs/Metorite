// ── Text colour (WS-61 KB-2p, §21.7) ────────────────────────────────────────
//
// Pure. Stored as `<span data-color="blue">text</span>`, or with a hex value
// (`<span data-color="#2563eb">`) that another tool wrote. The text inside is
// inline Markdown, so `**bold**` stays bold for every reader.
//
// The mark draws a `.kb-text-color` span. A name takes its colour from its
// token in `globals.css`. Only a hex value that `isHexColor` accepts reaches
// a `style`, and no other value is ever drawn (`colors.ts`).

import { Mark, type JSONContent } from "@tiptap/core";

import { hexStyle, isTextColor } from "./colors";
import { inlineFragment } from "./htmlFragment";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    textColor: {
      /** Colour the selected text. `null` takes the colour off. */
      setTextColor: (color: string | null) => ReturnType;
    };
  }
}

const OPEN = /^<span data-color="([^"<>]*)">/;

export const TextColor = Mark.create({
  name: "textColor",

  addAttributes() {
    return {
      color: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute("data-color"),
        renderHTML: (attrs: Record<string, unknown>) => {
          if (!isTextColor(attrs.color)) return {};
          const style = hexStyle("color", attrs.color);
          return { "data-color": attrs.color, ...(style ? { style } : {}) };
        },
      },
    };
  },

  parseHTML() {
    return [
      {
        tag: "span[data-color]",
        getAttrs: (el: HTMLElement) =>
          !el.hasAttribute("data-type") && isTextColor(el.getAttribute("data-color")) ? null : false,
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ["span", { ...HTMLAttributes, class: "kb-text-color" }, 0];
  },

  addCommands() {
    return {
      setTextColor:
        (color) =>
        ({ commands }) =>
          color && isTextColor(color) ? commands.setMark(this.name, { color }) : commands.unsetMark(this.name),
    };
  },

  markdownTokenizer: inlineFragment("textColor", "span", OPEN, (m) => (isTextColor(m[1]) ? { color: m[1] } : null)),

  parseMarkdown: (token, h) => h.applyMark("textColor", h.parseInline(token.tokens ?? []), token.attrs),

  renderMarkdown: (node: JSONContent, h) => {
    const color = node.attrs?.color;
    const inner = h.renderChildren(node);
    return isTextColor(color) ? `<span data-color="${color}">${inner}</span>` : inner;
  },
});
