// ── The page break (WS-61 KB-2p, §21.7) ─────────────────────────────────────
//
// Adapted from Docmost (AGPL-3.0), packages/editor-ext/src/lib/page-break/page-break.ts
//
// Pure. A block with no content, stored as one line:
// `<div data-type="page-break"></div>`. The editor draws a labelled rule,
// and the print style starts a new page there (`globals.css`).
//
// What changed from Docmost: the stored form and its Markdown reader and
// writer are ours, and the class name is the app's own.

import { Node } from "@tiptap/core";

import { blockStart } from "./htmlFragment";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    pageBreak: {
      /** Insert a page break. */
      setPageBreak: () => ReturnType;
    };
  }
}

/** The stored form, the whole line. */
export const PAGE_BREAK_HTML = '<div data-type="page-break"></div>';

const FORM = /^ {0,3}<div data-type="page-break"><\/div>[ \t]*(?:\n|$)/;

export const PageBreak = Node.create({
  name: "pageBreak",
  group: "block",
  atom: true,
  selectable: true,

  parseHTML() {
    return [{ tag: 'div[data-type="page-break"]' }];
  },

  renderHTML() {
    return ["div", { "data-type": "page-break", class: "kb-page-break", role: "separator", "aria-label": "Page break" }];
  },

  addCommands() {
    return {
      setPageBreak:
        () =>
        ({ chain }) =>
          chain().insertContent({ type: this.name }).focus().run(),
    };
  },

  markdownTokenizer: {
    name: "pageBreak",
    level: "block",
    start: (src: string) => blockStart(src, /\n {0,3}<div data-type="page-break">/),
    tokenize: (src) => {
      const m = FORM.exec(src);
      return m ? { type: "pageBreak", raw: m[0] } : undefined;
    },
  },

  parseMarkdown: (_token, h) => h.createNode("pageBreak"),

  renderMarkdown: () => PAGE_BREAK_HTML,
});
