// Adapted from Docmost (AGPL-3.0), packages/editor-ext/src/lib/table/dnd
// (dnd-extension.ts, utils.ts, calc-drag-over.ts and the two controllers in
// preview/). Docmost places the preview with @floating-ui. Here it is a
// fixed box at the pointer, so no new dependency enters the workbench.
//
// ── The table handles' state, and the drag of a row or a column ────────────
//    (WS-61 KB-2i, `knowledge_base.md` §21.7)
//
// The plugin keeps which cell the handles belong to: the cell under the
// pointer, or the cell of the caret. `TableHandles.tsx` draws a grip above
// that cell's column and one left of its row, and calls the drag API here.
// A drag shows a copy of the row or the column under the pointer, and a
// line where it will land. The drop moves it with `tableMove.ts`.
//
// ⚠️ No HTML is parsed: the copy is `cloneNode` of the drawn cells.

import { Extension, type Editor } from "@tiptap/core";
import type { Node as PMNode, ResolvedPos } from "@tiptap/pm/model";
import { Plugin, PluginKey, TextSelection, type Transaction } from "@tiptap/pm/state";
import { TableMap, cellAround } from "@tiptap/pm/tables";
import type { EditorView } from "@tiptap/pm/view";

import { findTable, moveColumn, moveRow } from "../../lib/editor/tableMove";

export type HandleAxis = "col" | "row";

export interface HoveringCellInfo {
  rowIndex: number;
  colIndex: number;
  cellPos: number;
  rowFirstCellPos: number;
  colFirstCellPos: number;
}

export interface TableHandleState {
  hoveringCell: HoveringCellInfo | null;
  tableNode: PMNode | null;
  tablePos: number | null;
  dragging: { orientation: HandleAxis; index: number } | null;
  /** A handle's menu is open: the handles stay on their cell. */
  frozen: boolean;
}

const INITIAL_STATE: TableHandleState = {
  hoveringCell: null,
  tableNode: null,
  tablePos: null,
  dragging: null,
  frozen: false,
};

export const TableHandlesKey = new PluginKey<TableHandleState>("kbTableHandles");

/** The cell info of a resolved position whose parent is a cell. */
export function cellInfoFromResolvedCell($cellPos: ResolvedPos): HoveringCellInfo {
  const map = TableMap.get($cellPos.node(-1));
  const tableStart = $cellPos.start(-1);
  const rect = map.findCell($cellPos.pos - tableStart);
  const at = (row: number, col: number) => tableStart + map.map[map.width * row + col];
  return {
    rowIndex: rect.top,
    colIndex: rect.left,
    cellPos: $cellPos.pos,
    rowFirstCellPos: at(rect.top, 0),
    colFirstCellPos: at(0, rect.left),
  };
}

function domCellAround(target: HTMLElement | null): HTMLElement | null {
  let node = target;
  while (node && node.nodeName !== "TD" && node.nodeName !== "TH") {
    node = node.classList?.contains("ProseMirror") ? null : (node.parentNode as HTMLElement | null);
  }
  return node;
}

/** How far outside the table the grips stay, in px. A grip sits on the edge. */
const GRIP_REACH_PX = 28;

/** True when the pointer is over the table at `tablePos`, or just outside it. */
function nearTable(view: EditorView, tablePos: number | null, event: MouseEvent): boolean {
  if (tablePos === null) return false;
  const dom = view.nodeDOM(tablePos);
  if (!(dom instanceof HTMLElement)) return false;
  const r = dom.getBoundingClientRect();
  return (
    event.clientX >= r.left - GRIP_REACH_PX &&
    event.clientX <= r.right + GRIP_REACH_PX &&
    event.clientY >= r.top - GRIP_REACH_PX &&
    event.clientY <= r.bottom + GRIP_REACH_PX
  );
}

function hoveringCellOf(view: EditorView, event: MouseEvent): HoveringCellInfo | undefined {
  const domCell = domCellAround(event.target as HTMLElement | null);
  if (!domCell) return undefined;
  let pos: number;
  try {
    pos = view.posAtDOM(domCell, 0);
  } catch {
    return undefined;
  }
  const $cellPos = cellAround(view.state.doc.resolve(pos));
  return $cellPos ? cellInfoFromResolvedCell($cellPos) : undefined;
}

/** The element of `index` along an axis that the pointer is over, or past. */
function dragOver(elements: Element[], pointer: number, axis: "x" | "y"): number {
  const startProp = axis === "x" ? "left" : "top";
  const endProp = axis === "x" ? "right" : "bottom";
  const last = elements.length - 1;
  return elements.findIndex((el, index) => {
    const rect = el.getBoundingClientRect();
    if (rect[startProp] <= pointer && pointer <= rect[endProp]) return true;
    if (index === last && pointer > rect[endProp]) return true;
    return index === 0 && pointer < rect[startProp];
  });
}

const LINE = 2;

/** The copy under the pointer and the line where the drop lands. */
class DragPreview {
  private preview: HTMLElement | null = null;
  private line: HTMLElement | null = null;
  private offset = { x: 0, y: 0 };

  start(table: HTMLTableElement, index: number, axis: HandleAxis, x: number, y: number) {
    this.end();
    const rows = Array.from(table.querySelectorAll("tr"));
    const first = axis === "row" ? rows[index]?.querySelector("th,td") : rows[0]?.querySelectorAll("th,td")[index];
    if (!first) return;
    const tableRect = table.getBoundingClientRect();
    const cellRect = first.getBoundingClientRect();

    const preview = document.createElement("div");
    preview.className = "kb-editor kb-table-drag-preview";
    const copy = document.createElement("table");
    const body = document.createElement("tbody");
    copy.appendChild(body);
    preview.appendChild(copy);
    if (axis === "row") {
      body.appendChild(rows[index].cloneNode(true));
    } else {
      for (const row of rows) {
        const cell = row.querySelectorAll("th,td")[index];
        if (!cell) continue;
        const tr = row.cloneNode(false);
        tr.appendChild(cell.cloneNode(true));
        body.appendChild(tr);
      }
    }
    Object.assign(preview.style, {
      width: `${axis === "row" ? tableRect.width : cellRect.width}px`,
      left: `${axis === "row" ? tableRect.left : cellRect.left}px`,
      top: `${axis === "row" ? cellRect.top : tableRect.top}px`,
    });
    this.offset = { x: x - cellRect.left, y: y - cellRect.top };

    const line = document.createElement("div");
    line.className = "kb-table-drop-line";
    Object.assign(line.style, {
      width: `${axis === "col" ? LINE : tableRect.width}px`,
      height: `${axis === "col" ? tableRect.height : LINE}px`,
      left: `${axis === "col" ? cellRect.left : tableRect.left}px`,
      top: `${axis === "col" ? tableRect.top : cellRect.top}px`,
    });
    document.body.append(preview, line);
    this.preview = preview;
    this.line = line;
  }

  /** The copy follows the pointer along its axis, kept inside the table. */
  move(table: HTMLTableElement, axis: HandleAxis, x: number, y: number) {
    if (!this.preview) return;
    const rect = table.getBoundingClientRect();
    const box = this.preview.getBoundingClientRect();
    if (axis === "col") {
      const left = Math.min(Math.max(x - this.offset.x, rect.left), rect.right - box.width);
      this.preview.style.left = `${left}px`;
      this.preview.style.top = `${rect.top}px`;
    } else {
      const top = Math.min(Math.max(y - this.offset.y, rect.top), rect.bottom - box.height);
      this.preview.style.top = `${top}px`;
      this.preview.style.left = `${rect.left}px`;
    }
  }

  /** The line on the near edge of the target, in the direction of the drag. */
  target(el: Element, axis: HandleAxis, before: boolean, table: HTMLTableElement) {
    if (!this.line) return;
    const rect = el.getBoundingClientRect();
    const tableRect = table.getBoundingClientRect();
    if (axis === "col") {
      this.line.style.left = `${(before ? rect.left : rect.right) - LINE / 2}px`;
      this.line.style.top = `${tableRect.top}px`;
    } else {
      this.line.style.top = `${(before ? rect.top : rect.bottom) - LINE / 2}px`;
      this.line.style.left = `${tableRect.left}px`;
    }
  }

  end() {
    this.preview?.remove();
    this.line?.remove();
    this.preview = null;
    this.line = null;
  }
}

/** The drag API that `TableHandles.tsx` calls. One for each editor. */
export class TableDrag {
  private preview = new DragPreview();
  private axis: HandleAxis = "col";
  private from = -1;
  private to = -1;
  private start = { x: 0, y: 0 };
  private table: HTMLTableElement | null = null;
  private dragging = false;

  constructor(private readonly editor: Editor) {}

  private dispatch(patch: Partial<TableHandleState>, tr: Transaction = this.editor.state.tr) {
    tr.setMeta(TableHandlesKey, patch);
    tr.setMeta("addToHistory", false);
    this.editor.view.dispatch(tr);
  }

  /** Start a drag of the row or column of the handles' cell. */
  begin(axis: HandleAxis, x: number, y: number): boolean {
    const state = TableHandlesKey.getState(this.editor.state);
    const cell = state?.hoveringCell;
    if (!cell) return false;
    const dom = this.editor.view.nodeDOM(cell.cellPos);
    const table = dom instanceof HTMLElement ? dom.closest("table") : null;
    if (!table) return false;
    this.axis = axis;
    this.from = axis === "col" ? cell.colIndex : cell.rowIndex;
    this.to = this.from;
    this.start = { x, y };
    this.table = table;
    this.dragging = true;
    this.preview.start(table, this.from, axis, x, y);
    // Park the caret in the table, so the drop finds it after any change.
    const tr = this.editor.state.tr;
    const here = findTable(this.editor.state.selection.$from);
    const there = findTable(this.editor.state.doc.resolve(cell.cellPos));
    if (there && (!here || here.pos !== there.pos)) {
      tr.setSelection(TextSelection.near(this.editor.state.doc.resolve(cell.cellPos + 1), 1));
    }
    this.dispatch({ dragging: { orientation: axis, index: this.from } }, tr);
    return true;
  }

  update(x: number, y: number) {
    const table = this.table;
    if (!this.dragging || !table) return;
    this.preview.move(table, this.axis, x, y);
    if (this.axis === "col") {
      const cells = Array.from(table.querySelector("tr")?.children ?? []);
      const index = dragOver(cells, x, "x");
      if (index < 0) return;
      this.to = index;
      this.preview.target(cells[index], "col", this.start.x > x, table);
    } else {
      const rows = Array.from(table.querySelectorAll("tr"));
      const index = dragOver(rows, y, "y");
      if (index < 0) return;
      this.to = index;
      this.preview.target(rows[index], "row", this.start.y > y, table);
    }
  }

  /** Drop: move the row or the column, then end the drag. */
  drop(x: number, y: number) {
    this.update(x, y);
    const { from, to } = this;
    if (this.dragging && from >= 0 && to >= 0 && from !== to) {
      const tr = this.editor.state.tr;
      const params = { tr, originIndex: from, targetIndex: to, select: true, pos: this.editor.state.selection.from };
      if (this.axis === "col" ? moveColumn(params) : moveRow(params)) this.editor.view.dispatch(tr);
    }
    this.cancel();
  }

  cancel() {
    const was = this.dragging;
    this.dragging = false;
    this.from = -1;
    this.to = -1;
    this.table = null;
    this.preview.end();
    if (was && !this.editor.isDestroyed) this.dispatch({ dragging: null });
  }

  /** Keep the handles on their cell while a handle's menu is open. */
  freeze(frozen: boolean) {
    if (this.editor.isDestroyed) return;
    const patch: Partial<TableHandleState> = { frozen };
    if (!frozen) Object.assign(patch, cellPatch(this.editor.state.selection.$head));
    this.dispatch(patch);
  }
}

/** The handles' cell from the caret, or none. */
function cellPatch($head: ResolvedPos): Partial<TableHandleState> {
  const $cell = cellAround($head);
  if (!$cell) return { hoveringCell: null, tableNode: null, tablePos: null };
  const table = findTable($cell);
  return { hoveringCell: cellInfoFromResolvedCell($cell), tableNode: table?.node ?? null, tablePos: table?.pos ?? null };
}

declare module "@tiptap/core" {
  interface Storage {
    kbTableHandles: { drag: TableDrag | null };
  }
}

/**
 * The plugin of the handles. It follows the pointer over a cell, and the
 * caret when the pointer leaves the table. A reader gets no handles.
 */
export const TableHandlePlugin = Extension.create<object, { drag: TableDrag | null }>({
  name: "kbTableHandles",

  addStorage() {
    return { drag: null };
  },

  addProseMirrorPlugins() {
    const editor = this.editor;
    const storage = this.storage;
    const dispatch = (view: EditorView, patch: Partial<TableHandleState>) => {
      const tr = view.state.tr.setMeta(TableHandlesKey, patch);
      tr.setMeta("addToHistory", false);
      view.dispatch(tr);
    };
    const same = (a: TableHandleState | undefined, cell: HoveringCellInfo | null) =>
      (a?.hoveringCell?.cellPos ?? null) === (cell?.cellPos ?? null);

    return [
      new Plugin<TableHandleState>({
        key: TableHandlesKey,
        state: {
          init: () => INITIAL_STATE,
          apply: (tr, prev) => {
            const meta = tr.getMeta(TableHandlesKey) as Partial<TableHandleState> | undefined;
            let next = prev;
            if (meta) {
              const changed = (Object.keys(meta) as (keyof TableHandleState)[]).some((k) => !Object.is(prev[k], meta[k]));
              next = changed ? { ...prev, ...meta } : prev;
            }
            // A change to the document moves the cell, or removes it.
            if (tr.docChanged && next.hoveringCell && !meta?.hoveringCell) {
              const pos = tr.mapping.map(next.hoveringCell.cellPos);
              try {
                next = { ...next, ...cellPatch(tr.doc.resolve(pos + 1)) };
              } catch {
                next = { ...next, hoveringCell: null, tableNode: null, tablePos: null };
              }
            }
            return next;
          },
        },
        view: () => {
          storage.drag = new TableDrag(editor);
          const onSelection = () => {
            if (!editor.isEditable) return;
            const current = TableHandlesKey.getState(editor.state);
            if (current?.frozen || current?.dragging) return;
            const patch = cellPatch(editor.state.selection.$head);
            if (!patch.hoveringCell || same(current, patch.hoveringCell)) return;
            dispatch(editor.view, patch);
          };
          editor.on("selectionUpdate", onSelection);
          return {
            destroy: () => {
              editor.off("selectionUpdate", onSelection);
              storage.drag?.cancel();
              storage.drag = null;
            },
          };
        },
        props: {
          handleDOMEvents: {
            pointermove: (view, event) => {
              const current = TableHandlesKey.getState(view.state);
              if (current?.frozen || current?.dragging || !view.editable) return false;
              const cell = hoveringCellOf(view, event);
              if (cell) {
                if (current?.hoveringCell?.cellPos === cell.cellPos) return false;
                const table = findTable(view.state.doc.resolve(cell.cellPos));
                dispatch(view, { hoveringCell: cell, tableNode: table?.node ?? null, tablePos: table?.pos ?? null });
                return false;
              }
              // The pointer left the cells. On its way to a grip it crosses
              // the table's edge, so the grips stay while it is near the
              // table, or the grip it reaches for would jump away.
              if (current?.hoveringCell && nearTable(view, current.tablePos, event)) return false;
              // Farther off, the grips go back to the caret's cell, or away.
              const patch = cellPatch(view.state.selection.$head);
              if (!same(current, patch.hoveringCell ?? null)) dispatch(view, patch);
              return false;
            },
          },
        },
      }),
    ];
  },
});

/** The drag API of an editor, once its view exists. */
export function tableDragOf(editor: Editor): TableDrag | null {
  return editor.storage.kbTableHandles?.drag ?? null;
}
