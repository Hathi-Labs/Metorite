// ── The status badge (WS-61 KB-2p, §21.7) ───────────────────────────────────
//
// Adapted from Docmost (AGPL-3.0), packages/editor-ext/src/lib/status.ts
//
// Pure. An inline badge with a short text and one of six colours, stored as
// `<span data-type="status" data-color="green">Done</span>`. The text is
// plain: a mark character in it gets a backslash, so a CommonMark reader
// sees the same words. A status with a colour off the list is not a stored
// form, and it stays raw text.
//
// What changed from Docmost: the stored form and its Markdown reader and
// writer are ours, the colour is checked against the list before it is
// drawn, and the node view comes from the component (`StatusView.tsx`).

import { Node, type JSONContent, type NodeViewRenderer } from "@tiptap/core";

import { escapeInlineText, unescapeInlineText } from "./htmlFragment";

/** The six colours of a status, in the order the popover shows them. */
export const STATUS_COLORS = ["gray", "blue", "green", "yellow", "red", "purple"] as const;
export type StatusColor = (typeof STATUS_COLORS)[number];

export function isStatusColor(value: unknown): value is StatusColor {
  return typeof value === "string" && (STATUS_COLORS as readonly string[]).includes(value);
}

/** The words of each colour, for the swatches. */
export const STATUS_COLOR_LABELS: Record<StatusColor, string> = {
  gray: "Gray",
  blue: "Blue",
  green: "Green",
  yellow: "Yellow",
  red: "Red",
  purple: "Purple",
};

/** The longest text a status keeps. A badge is a word or two. */
export const STATUS_MAX = 40;

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    status: {
      /** Insert a status badge. Its popover opens, so the member names it. */
      setStatus: (attrs?: { text?: string; color?: StatusColor }) => ReturnType;
    };
  }
  interface Storage {
    status: StatusStorage;
  }
}

export interface StatusStorage {
  /** True for the one status that the menu just inserted: its popover opens. */
  autoOpen: boolean;
}

const FORM = /^<span data-type="status" data-color="([a-z]+)">([^<\n]*)<\/span>/;

/** The status at the start of `src`, or `null`. */
export function readStatus(src: string): { raw: string; color: StatusColor; text: string } | null {
  const m = FORM.exec(src);
  if (!m || !isStatusColor(m[1])) return null;
  return { raw: m[0], color: m[1], text: unescapeInlineText(m[2]) };
}

/** The stored form of a status. */
export function writeStatus(text: string, color: unknown): string {
  const c = isStatusColor(color) ? color : "gray";
  return `<span data-type="status" data-color="${c}">${escapeInlineText(text)}</span>`;
}

export function createStatus(view?: NodeViewRenderer) {
  return Node.create<object, StatusStorage>({
    name: "status",
    group: "inline",
    inline: true,
    atom: true,
    selectable: true,
    draggable: true,

    addStorage() {
      return { autoOpen: false };
    },

    addAttributes() {
      return {
        text: {
          default: "",
          parseHTML: (el: HTMLElement) => (el.textContent ?? "").slice(0, STATUS_MAX),
          rendered: false,
        },
        color: {
          default: "gray",
          parseHTML: (el: HTMLElement) => {
            const c = el.getAttribute("data-color");
            return isStatusColor(c) ? c : "gray";
          },
          rendered: false,
        },
      };
    },

    parseHTML() {
      return [{ tag: 'span[data-type="status"]', priority: 60 }];
    },

    renderHTML({ node }) {
      const color = isStatusColor(node.attrs.color) ? node.attrs.color : "gray";
      return ["span", { "data-type": "status", "data-color": color, class: "kb-status" }, String(node.attrs.text ?? "")];
    },

    renderText({ node }) {
      return String(node.attrs.text ?? "");
    },

    ...(view ? { addNodeView: () => view } : {}),

    addCommands() {
      return {
        setStatus:
          (attrs) =>
          ({ commands }) => {
            this.storage.autoOpen = true;
            return commands.insertContent({
              type: this.name,
              attrs: { text: attrs?.text ?? "", color: isStatusColor(attrs?.color) ? attrs?.color : "gray" },
            });
          },
      };
    },

    markdownTokenizer: {
      name: "status",
      level: "inline",
      start: (src: string) => src.indexOf('<span data-type="status"'),
      tokenize: (src) => {
        const found = readStatus(src);
        return found ? { type: "status", raw: found.raw, color: found.color, text: found.text } : undefined;
      },
    },

    parseMarkdown: (token, h) =>
      h.createNode("status", { text: String(token.text ?? ""), color: isStatusColor(token.color) ? token.color : "gray" }),

    renderMarkdown: (node: JSONContent) => writeStatus(String(node.attrs?.text ?? ""), node.attrs?.color),
  });
}
