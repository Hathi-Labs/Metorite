// ── The subpages list (WS-61 KB-2p, §21.7) ──────────────────────────────────
//
// Adapted from Docmost (AGPL-3.0), packages/editor-ext/src/lib/subpages/subpages.ts
// and apps/client/src/features/editor/components/subpages/subpages.utils.ts
//
// Pure. A block that lists the child pages of the open page, stored as one
// line: `<div data-type="subpages" data-order="manual"></div>`. The body
// keeps only the order. The node view reads the children from the pages list
// that the Knowledge API already serves (`SubpagesView.tsx`), so the list
// follows the tree with no save.
//
// What changed from Docmost: the stored form and its Markdown reader and
// writer are ours, and the orders are Manual, A to Z and Recent.

import { Node, type JSONContent, type NodeViewRenderer } from "@tiptap/core";

import { blockStart } from "./htmlFragment";

/** The orders of the list. Manual is the order of the tree. */
export const SUBPAGE_ORDERS = ["manual", "title", "recent"] as const;
export type SubpageOrder = (typeof SUBPAGE_ORDERS)[number];

export const SUBPAGE_ORDER_LABELS: Record<SubpageOrder, string> = {
  manual: "Manual",
  title: "A to Z",
  recent: "Recent",
};

export function isSubpageOrder(value: unknown): value is SubpageOrder {
  return typeof value === "string" && (SUBPAGE_ORDERS as readonly string[]).includes(value);
}

/** One child page, as the list needs it. */
export interface SubpageRow {
  id: string;
  title: string;
  position: number;
  updated_at: string | null;
}

/** The children in the chosen order. A tie keeps the title order. */
export function sortSubpages<T extends SubpageRow>(rows: readonly T[], order: SubpageOrder): T[] {
  const byTitle = (a: T, b: T) => a.title.localeCompare(b.title, undefined, { sensitivity: "base", numeric: true });
  const out = [...rows];
  if (order === "title") return out.sort((a, b) => byTitle(a, b) || a.id.localeCompare(b.id));
  if (order === "recent") {
    return out.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? "") || byTitle(a, b));
  }
  return out.sort((a, b) => a.position - b.position || byTitle(a, b));
}

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    subpages: {
      /** Insert the list of sub-pages, in the order of the tree. */
      insertSubpages: (order?: SubpageOrder) => ReturnType;
    };
  }
}

const FORM = /^ {0,3}<div data-type="subpages" data-order="([a-z]+)"><\/div>[ \t]*(?:\n|$)/;

/** The stored form of the list. */
export function writeSubpages(order: unknown): string {
  return `<div data-type="subpages" data-order="${isSubpageOrder(order) ? order : "manual"}"></div>`;
}

export function createSubpages(view?: NodeViewRenderer) {
  return Node.create({
    name: "subpages",
    group: "block",
    atom: true,
    draggable: true,
    isolating: true,

    addAttributes() {
      return {
        order: {
          default: "manual",
          parseHTML: (el: HTMLElement) => {
            const value = el.getAttribute("data-order");
            return isSubpageOrder(value) ? value : "manual";
          },
          renderHTML: (attrs: Record<string, unknown>) => ({
            "data-order": isSubpageOrder(attrs.order) ? attrs.order : "manual",
          }),
        },
      };
    },

    parseHTML() {
      return [{ tag: 'div[data-type="subpages"]' }];
    },

    renderHTML({ HTMLAttributes }) {
      return ["div", { ...HTMLAttributes, "data-type": "subpages", class: "kb-subpages" }];
    },

    ...(view ? { addNodeView: () => view } : {}),

    addCommands() {
      return {
        insertSubpages:
          (order = "manual") =>
          ({ commands }) =>
            commands.insertContent({ type: this.name, attrs: { order: isSubpageOrder(order) ? order : "manual" } }),
      };
    },

    markdownTokenizer: {
      name: "subpages",
      level: "block",
      start: (src: string) => blockStart(src, /\n {0,3}<div data-type="subpages"/),
      tokenize: (src) => {
        const m = FORM.exec(src);
        return m && isSubpageOrder(m[1]) ? { type: "subpages", raw: m[0], order: m[1] } : undefined;
      },
    },

    parseMarkdown: (token, h) => h.createNode("subpages", { order: token.order }),

    renderMarkdown: (node: JSONContent) => writeSubpages(node.attrs?.order),
  });
}
