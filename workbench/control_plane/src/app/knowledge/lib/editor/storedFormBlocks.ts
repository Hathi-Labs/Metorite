// ── The `/` menu entries of the stored forms (WS-61 KB-2p, §21.7) ───────────
//
// Pure. `BLOCKS` in `blocks.ts` spreads this list, so the `/` menu offers
// each block of KB-2p. The names and the search words follow Docmost's
// slash menu, in our words.

import type { Editor, Range } from "@tiptap/core";

import type { BlockItem } from "./blocks";
import { MAX_COLUMNS, MIN_COLUMNS } from "./columns";

/** Delete the typed `/query`, then act. */
const at = (editor: Editor, range: Range) => editor.chain().focus().deleteRange(range);

const COLUMN_BLOCKS: BlockItem[] = Array.from({ length: MAX_COLUMNS - MIN_COLUMNS + 1 }, (_, i) => {
  const n = MIN_COLUMNS + i;
  return {
    id: `columns-${n}`,
    label: `${n} columns`,
    keywords: "columns layout split side by side grid",
    icon: n === 2 ? "Columns2" : n === 3 ? "Columns3" : "Columns4",
    kind: "insert",
    run: (e: Editor, r: Range) => at(e, r).insertColumns(n).run(),
  };
});

export const STORED_FORM_BLOCKS: readonly BlockItem[] = [
  { id: "status", label: "Status", keywords: "badge label lozenge state tag", icon: "Tag", kind: "insert",
    run: (e, r) => at(e, r).setStatus().run() },
  { id: "date", label: "Date", keywords: "today day calendar when due", icon: "Calendar", kind: "insert",
    run: (e, r) => at(e, r).insertDate().run() },
  ...COLUMN_BLOCKS,
  { id: "subpages", label: "Sub-pages", keywords: "subpages child children pages nested hierarchy list",
    icon: "Files", kind: "insert", run: (e, r) => at(e, r).insertSubpages().run() },
  { id: "page-break", label: "Page break", keywords: "pagebreak print new page", icon: "SeparatorHorizontal",
    kind: "insert", run: (e, r) => at(e, r).setPageBreak().run() },
];
