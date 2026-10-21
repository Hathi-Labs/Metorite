// ── The rules of a GFM table in the editor (WS-61 KB-2i, §21.7) ────────────
//
// Adapted from Docmost (AGPL-3.0),
// apps/client/src/features/editor/components/table/handle/hooks/use-table-clear.ts
// (`clearCells`). The rest of this file is ours.
//
// Pure. A GFM table has one header row, and an alignment for each column.
// The editor keeps both, so the table menu may change them and a save keeps
// the change:
//
// - A header row with no text reads as a table with no header. GFM needs a
//   header line, so the editor writes an empty one for a table whose header
//   is off (`renderTable` in `schema.ts`). Read back, that line is the same
//   table again, with the header still off.
// - The alignment of a column is the `align` of each of its cells, and the
//   rule line writes it (`:---`, `:---:`, `---:`).

import type { JSONContent } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import type { Transaction } from "@tiptap/pm/state";
import { TableMap } from "@tiptap/pm/tables";

/** The alignments a GFM column can have. `null` is the default, `---`. */
export type ColumnAlign = "left" | "center" | "right" | null;

export const COLUMN_ALIGNS: ReadonlyArray<{ value: Exclude<ColumnAlign, null>; label: string; icon: string }> = [
  { value: "left", label: "Align left", icon: "AlignLeft" },
  { value: "center", label: "Align center", icon: "AlignCenter" },
  { value: "right", label: "Align right", icon: "AlignRight" },
];

/** True when a node holds no text, no image and no raw piece. */
function isBlank(node: JSONContent): boolean {
  if (node.type === "text") return !(node.text ?? "").trim();
  if (node.type === "image" || node.type === "htmlInline" || node.type === "footnoteReference") return false;
  return (node.content ?? []).every(isBlank);
}

/**
 * A table whose header row holds no text, with that row taken off. A table
 * with only that row keeps it, because a table needs one row.
 */
export function dropEmptyHeaderRow(node: JSONContent): JSONContent {
  const content = node.content?.map(dropEmptyHeaderRow);
  const out = content ? { ...node, content } : node;
  if (out.type !== "table" || !out.content || out.content.length < 2) return out;
  const [first, ...rest] = out.content;
  const cells = first.content ?? [];
  if (cells.length && cells.every((c) => c.type === "tableHeader" && isBlank(c))) return { ...out, content: rest };
  return out;
}

/** The alignment of column `index`: the first cell of it that has one. */
export function columnAlign(table: PMNode, index: number): ColumnAlign {
  const map = TableMap.get(table);
  if (index < 0 || index >= map.width) return null;
  for (let row = 0; row < map.height; row++) {
    const cell = table.nodeAt(map.map[row * map.width + index]);
    const align = cell?.attrs.align;
    if (align === "left" || align === "center" || align === "right") return align;
  }
  return null;
}

/**
 * Set the alignment of column `index` of the table at `tablePos`. Each cell
 * of the column takes it, so the rule line and the screen agree.
 */
export function setColumnAlign(tr: Transaction, tablePos: number, index: number, align: ColumnAlign): boolean {
  const table = tr.doc.nodeAt(tablePos);
  if (!table || table.type.spec.tableRole !== "table") return false;
  const map = TableMap.get(table);
  if (index < 0 || index >= map.width) return false;
  const seen = new Set<number>();
  for (let row = 0; row < map.height; row++) {
    const offset = map.map[row * map.width + index];
    if (seen.has(offset)) continue;
    seen.add(offset);
    const pos = tablePos + 1 + offset;
    const cell = tr.doc.nodeAt(pos);
    if (cell) tr.setNodeMarkup(pos, undefined, { ...cell.attrs, align });
  }
  return true;
}

/** Empty each cell of a row or a column. A cell keeps one empty paragraph. */
export function clearCells(
  tr: Transaction,
  tablePos: number,
  scope: { kind: "row" | "col"; index: number },
): boolean {
  const table = tr.doc.nodeAt(tablePos);
  const paragraph = tr.doc.type.schema.nodes.paragraph;
  if (!table || !paragraph) return false;
  const map = TableMap.get(table);
  const offsets = new Set<number>();
  if (scope.kind === "col") {
    for (let row = 0; row < map.height; row++) offsets.add(map.map[row * map.width + scope.index]);
  } else {
    for (let col = 0; col < map.width; col++) offsets.add(map.map[scope.index * map.width + col]);
  }
  // Last first, so an earlier change does not move a later cell.
  for (const offset of [...offsets].sort((a, b) => b - a)) {
    const pos = tablePos + 1 + offset;
    const cell = tr.doc.nodeAt(pos);
    if (cell) tr.replaceWith(pos + 1, pos + cell.nodeSize - 1, paragraph.create());
  }
  return tr.docChanged;
}
