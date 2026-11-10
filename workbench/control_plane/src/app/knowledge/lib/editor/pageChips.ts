// ── The page mention chip (WS-61) ───────────────────────────────────────────
//
// A link to `/knowledge/pages/<uuid>` draws as a Notion-style mention: a page
// glyph, then the title, with no underline. The stored form does not change.
// It stays a Markdown link, and KB-2h reads it as before.
//
// Two parts draw it. `KnowledgeLink` in `schema.ts` puts the class
// `kb-page-chip` on the `<a>` of such a link. This extension adds the glyph
// as a decoration on the first text of each link, because the page emoji is
// known only after the page list loads. With no emoji, the CSS draws a file
// icon from a mask on `currentColor`. No colour is written here.

import { Extension } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

import { chipPageId } from "./pageLinks";

export interface ChipRun {
  /** The first text node of the link: where the glyph goes. */
  from: number;
  to: number;
  pageId: string;
}

/**
 * The first text node of each run of one page link in the document. Text
 * nodes that follow it with the same page (a bold word inside the title)
 * belong to the same run and get no second glyph.
 */
export function chipRuns(doc: PMNode): ChipRun[] {
  const runs: ChipRun[] = [];
  let lastEnd = -1;
  let lastId: string | null = null;
  doc.descendants((node, pos) => {
    if (!node.isText) {
      lastId = null;
      return true;
    }
    const link = node.marks.find((m) => m.type.name === "link");
    const id = link ? chipPageId(String(link.attrs.href ?? "")) : null;
    if (!id) {
      lastId = null;
      return false;
    }
    if (!(lastId === id && lastEnd === pos)) runs.push({ from: pos, to: pos + node.nodeSize, pageId: id });
    lastId = id;
    lastEnd = pos + node.nodeSize;
    return false;
  });
  return runs;
}

/** True when the document holds at least one chip. */
export function hasChips(doc: PMNode): boolean {
  return chipRuns(doc).length > 0;
}

const KEY = new PluginKey("kbPageChips");

export interface PageChipStorage {
  /** page id to emoji. The component fills it once the page list loads. */
  icons: Map<string, string>;
}

declare module "@tiptap/core" {
  interface Storage {
    kbPageChips: PageChipStorage;
  }
}

/** Draw the glyph of each page chip. */
export const PageChips = Extension.create<object, PageChipStorage>({
  name: "kbPageChips",
  addStorage() {
    return { icons: new Map<string, string>() };
  },
  addProseMirrorPlugins() {
    const storage = this.storage;
    return [
      new Plugin({
        key: KEY,
        props: {
          decorations: (state) => {
            const runs = chipRuns(state.doc);
            if (!runs.length) return null;
            return DecorationSet.create(
              state.doc,
              runs.map((r) => {
                const emoji = storage.icons.get(r.pageId);
                return Decoration.inline(r.from, r.to, {
                  class: "kb-page-glyph",
                  ...(emoji ? { "data-emoji": emoji } : {}),
                });
              }),
            );
          },
        },
      }),
    ];
  },
});
