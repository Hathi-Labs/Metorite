// ── The blocks and marks with a stored form (WS-61 KB-2p, §21.7) ────────────
//
// Pure. One list, which `knowledgeExtensions` in `schema.ts` registers after
// the raw HTML nodes. The order matters: marked tries the tokenizer that
// registered LAST first, so each exact shape here wins over `htmlInline`,
// which takes any tag. Each block and mark lives in its own file.

import type { AnyExtension, NodeViewRenderer } from "@tiptap/core";

import { Column, createColumns } from "./columns";
import { createDate } from "./date";
import { Highlight } from "./highlight";
import { KbHtmlTable } from "./htmlTable";
import { PageBreak } from "./pageBreak";
import { createStatus } from "./status";
import { createSubpages } from "./subpages";
import { Subscript, Superscript } from "./supSub";
import { KbTextAlign } from "./textAlign";
import { TextColor } from "./textColor";

/** The node views of the stored forms. The component passes them, and the test passes none. */
export interface StoredFormViews {
  status?: NodeViewRenderer;
  date?: NodeViewRenderer;
  columns?: NodeViewRenderer;
  subpages?: NodeViewRenderer;
}

/** The extensions of the stored forms, in registration order. */
export function storedFormExtensions(views: StoredFormViews = {}): AnyExtension[] {
  return [
    TextColor,
    Highlight,
    Superscript,
    Subscript,
    KbTextAlign,
    createStatus(views.status),
    createDate(views.date),
    createColumns(views.columns),
    Column,
    createSubpages(views.subpages),
    PageBreak,
    KbHtmlTable,
  ];
}
