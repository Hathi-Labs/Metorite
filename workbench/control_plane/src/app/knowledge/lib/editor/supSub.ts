// ── Superscript and subscript (WS-61 KB-2p, §21.7) ──────────────────────────
//
// Pure. Stored as `<sup>text</sup>` and `<sub>text</sub>`, with no attribute.
// The text inside is inline Markdown. One excludes the other, as in a word
// processor: a superscript turns off the subscript of the same text.

import { Mark, type JSONContent } from "@tiptap/core";

import { inlineFragment } from "./htmlFragment";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    superscript: {
      /** Turn superscript on or off for the selection. */
      toggleSuperscript: () => ReturnType;
    };
    subscript: {
      /** Turn subscript on or off for the selection. */
      toggleSubscript: () => ReturnType;
    };
  }
}

function scriptMark(name: "superscript" | "subscript", tag: "sup" | "sub", other: string) {
  const open = new RegExp(`^<${tag}>`);
  return Mark.create({
    name,
    excludes: other,

    parseHTML() {
      return [{ tag }];
    },

    renderHTML() {
      return [tag, 0];
    },

    addCommands() {
      const toggle = name === "superscript" ? "toggleSuperscript" : "toggleSubscript";
      return {
        [toggle]:
          () =>
          ({ commands }: { commands: { toggleMark: (n: string) => boolean } }) =>
            commands.toggleMark(name),
      };
    },

    markdownTokenizer: inlineFragment(name, tag, open, () => ({})),

    parseMarkdown: (token, h) => h.applyMark(name, h.parseInline(token.tokens ?? [])),

    renderMarkdown: (node: JSONContent, h) => `<${tag}>${h.renderChildren(node)}</${tag}>`,
  });
}

export const Superscript = scriptMark("superscript", "sup", "subscript");
export const Subscript = scriptMark("subscript", "sub", "superscript");
