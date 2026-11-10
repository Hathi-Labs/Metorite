"use client";

/**
 * Adapted from Docmost (AGPL-3.0),
 * apps/client/src/features/editor/components/table/handle (column-handle.tsx,
 * row-handle.tsx, table-handles-layer.tsx, menus/column-handle-menu.tsx,
 * menus/row-handle-menu.tsx, hooks/use-table-move-row-column.ts,
 * hooks/use-column-row-menu-lifecycle.ts and lib/select-row-column.ts).
 * Docmost draws these with Mantine and Tabler. Here they draw with our
 * `Button`, `ContextMenu` and `<Icon>`, and the colours are tokens.
 *
 * ── The grips of a table (WS-61 KB-2i, `knowledge_base.md` §21.7) ─────────
 *
 * A grip sits above the column and left of the row of the cell under the
 * pointer, or of the caret. Drag a grip to move the column or the row. Click
 * it, or press Enter on it, for its menu:
 *
 * - Column: the alignment (GFM keeps it), add a column left or right, clear
 *   the cells, delete the column, move it left or right.
 * - Row: the header row (on the first row), add a row above or below, clear
 *   the cells, delete the row, move it up or down.
 *
 * Docmost's colour and sort items are left out: GFM keeps no cell colour,
 * and KB-2p owns the stored form of the table features GFM cannot hold.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { CellSelection, TableMap } from "@tiptap/pm/tables";
import { useEditorState } from "@tiptap/react";

import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import Icon, { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";

import { moveColumn, moveRow } from "../../lib/editor/tableMove";
import { COLUMN_ALIGNS, clearCells, columnAlign, setColumnAlign, type ColumnAlign } from "../../lib/editor/tables";
import { TableHandlesKey, tableDragOf, type HandleAxis, type TableHandleState } from "./tableDrag";

/** How far a pointer moves on a grip before a press becomes a drag. */
const DRAG_AFTER_PX = 4;

/** The selection of a whole row or column, so the menu acts on it. */
function rowOrColumnSelection(editor: Editor, table: PMNode, tablePos: number, axis: HandleAxis, index: number) {
  const map = TableMap.get(table);
  const start = tablePos + 1;
  const doc = editor.state.doc;
  if (axis === "col") {
    if (index < 0 || index >= map.width) return null;
    const $first = doc.resolve(start + map.map[index]);
    const $last = doc.resolve(start + map.map[(map.height - 1) * map.width + index]);
    return CellSelection.colSelection($first, $last);
  }
  if (index < 0 || index >= map.height) return null;
  const $first = doc.resolve(start + map.map[index * map.width]);
  const $last = doc.resolve(start + map.map[index * map.width + (map.width - 1)]);
  return CellSelection.rowSelection($first, $last);
}

/** True when the first row of the table is a header row. */
function hasHeaderRow(table: PMNode): boolean {
  const first = table.firstChild;
  if (!first || !first.childCount) return false;
  let header = true;
  first.forEach((cell) => {
    if (cell.type.name !== "tableHeader") header = false;
  });
  return header;
}

/** The menu of a grip, as items for the shared `ContextMenu`. */
export function handleMenuItems(
  editor: Editor,
  axis: HandleAxis,
  index: number,
  table: PMNode,
  tablePos: number,
): CtxItem[] {
  const map = TableMap.get(table);
  const last = axis === "col" ? map.width - 1 : map.height - 1;
  const move = (to: number) => {
    const tr = editor.state.tr;
    const params = { tr, originIndex: index, targetIndex: to, select: true, pos: tablePos + 1 };
    if (axis === "col" ? moveColumn(params) : moveRow(params)) editor.view.dispatch(tr);
    editor.commands.focus();
  };
  const clear = () => {
    const tr = editor.state.tr;
    if (clearCells(tr, tablePos, { kind: axis, index })) editor.view.dispatch(tr);
    editor.commands.focus();
  };
  const item = (label: string, icon: string, onSelect: () => void, extra: Partial<CtxItem> = {}): CtxItem =>
    ({ kind: "item", label, icon: themedIcon(icon), onSelect, ...extra }) as CtxItem;
  const sep: CtxItem = { kind: "sep" };

  if (axis === "col") {
    const current = columnAlign(table, index);
    const align = (value: ColumnAlign) => {
      const tr = editor.state.tr;
      // A second pick of the same alignment goes back to the default.
      if (setColumnAlign(tr, tablePos, index, value === current ? null : value)) editor.view.dispatch(tr);
      editor.commands.focus();
    };
    return [
      ...COLUMN_ALIGNS.map((a) => item(a.label, a.icon, () => align(a.value), { checked: current === a.value })),
      sep,
      item("Add column left", "BetweenVerticalStart", () => editor.chain().focus().addColumnBefore().run()),
      item("Add column right", "BetweenVerticalEnd", () => editor.chain().focus().addColumnAfter().run()),
      sep,
      item("Clear cells", "Eraser", clear),
      item("Delete column", "Trash2", () => editor.chain().focus().deleteColumn().run(), { danger: true }),
      sep,
      ...(index > 0 ? [item("Move column left", "ArrowLeft", () => move(index - 1))] : []),
      ...(index < last ? [item("Move column right", "ArrowRight", () => move(index + 1))] : []),
    ];
  }
  return [
    ...(index === 0
      ? [
          item("Header row", "PanelTop", () => editor.chain().focus().toggleHeaderRow().run(), {
            checked: hasHeaderRow(table),
          }),
          sep,
        ]
      : []),
    item("Add row above", "BetweenHorizontalStart", () => editor.chain().focus().addRowBefore().run()),
    item("Add row below", "BetweenHorizontalEnd", () => editor.chain().focus().addRowAfter().run()),
    sep,
    item("Clear cells", "Eraser", clear),
    item("Delete row", "Trash2", () => editor.chain().focus().deleteRow().run(), { danger: true }),
    sep,
    ...(index > 0 ? [item("Move row up", "ArrowUp", () => move(index - 1))] : []),
    ...(index < last ? [item("Move row down", "ArrowDown", () => move(index + 1))] : []),
  ];
}

interface GripProps {
  editor: Editor;
  axis: HandleAxis;
  index: number;
  table: PMNode;
  tablePos: number;
  /** Where the grip sits, inside the editor shell. */
  style: React.CSSProperties;
}

/** One grip: a drag, or a click for its menu. */
function Grip({ editor, axis, index, table, tablePos, style }: GripProps) {
  const [menu, setMenu] = useState<{ x: number; y: number; keyboard: boolean } | null>(null);
  const press = useRef<{ x: number; y: number; id: number; dragging: boolean } | null>(null);
  const dragged = useRef(false);
  const wrap = useRef<HTMLSpanElement | null>(null);
  const button = () => wrap.current?.querySelector("button") ?? null;
  const label = axis === "col" ? `Column ${index + 1} actions` : `Row ${index + 1} actions`;

  const open = (keyboard: boolean) => {
    const rect = button()?.getBoundingClientRect();
    const selection = rowOrColumnSelection(editor, table, tablePos, axis, index);
    if (selection) editor.view.dispatch(editor.state.tr.setSelection(selection));
    tableDragOf(editor)?.freeze(true);
    setMenu({ x: rect ? (axis === "col" ? rect.left : rect.right + 4) : 0, y: rect ? (axis === "col" ? rect.bottom + 4 : rect.top) : 0, keyboard });
  };

  return (
    <span ref={wrap} className={`kb-table-handle is-${axis}`} style={style} data-kb-table-handle={axis}>
      <Button
        variant="secondary"
        size="none"
        layout="flex h-full w-full items-center justify-center"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={menu ? true : undefined}
        title={axis === "col" ? "Drag to move the column, or click for its menu" : "Drag to move the row, or click for its menu"}
        // The caret stays in the table while a member presses a grip.
        onMouseDown={(e) => e.preventDefault()}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          press.current = { x: e.clientX, y: e.clientY, id: e.pointerId, dragging: false };
          dragged.current = false;
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const p = press.current;
          if (!p || p.id !== e.pointerId) return;
          const drag = tableDragOf(editor);
          if (!p.dragging && Math.hypot(e.clientX - p.x, e.clientY - p.y) >= DRAG_AFTER_PX) {
            p.dragging = Boolean(drag?.begin(axis, p.x, p.y));
          }
          if (p.dragging) drag?.update(e.clientX, e.clientY);
        }}
        onPointerUp={(e) => {
          const p = press.current;
          press.current = null;
          if (!p || p.id !== e.pointerId) return;
          if (p.dragging) {
            dragged.current = true;
            tableDragOf(editor)?.drop(e.clientX, e.clientY);
            editor.commands.focus();
          }
        }}
        onPointerCancel={() => {
          press.current = null;
          tableDragOf(editor)?.cancel();
        }}
        onClick={(e) => {
          // A drag ends with a click on the grip. It opens no menu.
          if (dragged.current) {
            dragged.current = false;
            return;
          }
          open(e.detail === 0);
        }}
      >
        <Icon name={axis === "col" ? "GripHorizontal" : "GripVertical"} size={12} />
      </Button>
      {menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          autoFocus={menu.keyboard}
          items={handleMenuItems(editor, axis, index, table, tablePos)}
          onClose={() => {
            setMenu(null);
            tableDragOf(editor)?.freeze(false);
            if (menu.keyboard) button()?.focus();
          }}
        />
      ) : null}
    </span>
  );
}

const NO_STATE: TableHandleState = { hoveringCell: null, tableNode: null, tablePos: null, dragging: null, frozen: false };

/**
 * The two grips of the handles' cell. `container` is the editor shell, the
 * box the grips are placed in.
 */
export function TableHandles({ editor, container }: { editor: Editor; container: HTMLElement | null }) {
  const state =
    useEditorState({ editor, selector: ({ editor: e }) => (e ? (TableHandlesKey.getState(e.state) ?? NO_STATE) : NO_STATE) }) ??
    NO_STATE;

  // A scroll of the table or a resize moves the cells under still grips.
  const [, setTick] = useState(0);
  const remeasure = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    window.addEventListener("resize", remeasure);
    document.addEventListener("scroll", remeasure, true);
    return () => {
      window.removeEventListener("resize", remeasure);
      document.removeEventListener("scroll", remeasure, true);
    };
  }, [remeasure]);

  const cell = state.hoveringCell;
  // Measured on each render: the plugin state changes on each move of the
  // cell and each edit of the table, and a scroll sets the tick.
  const place = (() => {
    if (!cell || !container || !editor.isEditable || editor.isDestroyed) return null;
    const box = container.getBoundingClientRect();
    const node = (pos: number) => {
      const dom = editor.view.nodeDOM(pos);
      return dom instanceof HTMLElement ? dom : null;
    };
    const colCell = node(cell.colFirstCellPos);
    const rowCell = node(cell.rowFirstCellPos);
    if (!colCell || !rowCell) return null;
    const wrapper = colCell.closest(".tableWrapper")?.getBoundingClientRect() ?? null;
    const c = colCell.getBoundingClientRect();
    const r = rowCell.getBoundingClientRect();
    const middle = c.left + c.width / 2;
    const colShown = !wrapper || (middle >= wrapper.left && middle <= wrapper.right);
    return {
      col: colShown ? { left: middle - box.left, top: c.top - box.top } : null,
      row: { left: r.left - box.left, top: r.top - box.top + r.height / 2 },
    };
  })();

  if (!place || !cell || !state.tableNode || state.tablePos === null) return null;
  return (
    <>
      {place.col ? (
        <Grip
          key={`col-${state.tablePos}-${cell.colIndex}`}
          editor={editor}
          axis="col"
          index={cell.colIndex}
          table={state.tableNode}
          tablePos={state.tablePos}
          style={{ left: place.col.left, top: place.col.top }}
        />
      ) : null}
      <Grip
        key={`row-${state.tablePos}-${cell.rowIndex}`}
        editor={editor}
        axis="row"
        index={cell.rowIndex}
        table={state.tableNode}
        tablePos={state.tablePos}
        style={{ left: place.row.left, top: place.row.top }}
      />
    </>
  );
}
