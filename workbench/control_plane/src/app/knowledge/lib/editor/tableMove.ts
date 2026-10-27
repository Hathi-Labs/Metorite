// Adapted from Docmost (AGPL-3.0), packages/editor-ext/src/lib/table/utils
// (query.ts, transpose.ts, convert-table-node-to-array-of-rows.ts,
// convert-array-of-rows-to-table-node.ts, move-row-in-array-of-rows.ts,
// get-cells-in-column.ts, get-cells-in-row.ts, get-selection-range-in-column.ts,
// get-selection-range-in-row.ts, move-column.ts and move-row.ts).
//
// `getSelectionRangeInColumn` and `getSelectionRangeInRow` carry this note
// upstream: "Original implementation from Atlassian (Apache License 2.0)",
// https://bitbucket.org/atlassian/atlassian-frontend-mirror/src/5f91cb871e8248bc3bae5ddc30bb9fd9200fadbb/editor/editor-tables/src/utils/
//
// ── Move a row or a column of a table (WS-61 KB-2i, §21.7) ─────────────────
//
// Pure ProseMirror, so a test runs it in node. A move takes the cells with
// it, and each cell keeps its attributes, so the alignment of a column moves
// with the column. The header row stays the first row: a row moved to the
// top takes the header type of that place, as in Docmost.

import type { Node as PMNode, ResolvedPos } from "@tiptap/pm/model";
import { TextSelection, type Selection, type Transaction } from "@tiptap/pm/state";
import { CellSelection, TableMap } from "@tiptap/pm/tables";

export interface FindParentNodeResult {
  /** The closest parent node that satisfies the predicate. */
  node: PMNode;
  /** The position directly before the node. */
  pos: number;
  /** The position at the start of the node. */
  start: number;
  /** The depth of the node. */
  depth: number;
}

/** The closest parent node that satisfies the predicate. */
export function findParentNode(predicate: (node: PMNode) => boolean, $pos: ResolvedPos): FindParentNodeResult | undefined {
  for (let depth = $pos.depth; depth >= 0; depth -= 1) {
    const node = $pos.node(depth);
    if (predicate(node)) {
      const pos = depth === 0 ? 0 : $pos.before(depth);
      const start = $pos.start(depth);
      return { node, pos, start, depth };
    }
  }
  return undefined;
}

/** The closest table around a position. */
export function findTable($pos: ResolvedPos): FindParentNodeResult | undefined {
  return findParentNode((node) => node.type.spec.tableRole === "table", $pos);
}

/** Flip a 2D array along its diagonal: columns become rows. */
export function transpose<T>(array: T[][]): T[][] {
  return array[0].map((_, i) => array.map((column) => column[i]));
}

/**
 * The table as a matrix of rows and columns. A cell that a merged cell
 * covers is `null`, so each row has the width of the table.
 */
export function convertTableNodeToArrayOfRows(tableNode: PMNode): (PMNode | null)[][] {
  const map = TableMap.get(tableNode);
  const rows: (PMNode | null)[][] = [];
  for (let rowIndex = 0; rowIndex < map.height; rowIndex++) {
    const row: (PMNode | null)[] = [];
    for (let colIndex = 0; colIndex < map.width; colIndex++) {
      const cellIndex = rowIndex * map.width + colIndex;
      const cellPos = map.map[cellIndex];
      if (rowIndex > 0 && cellPos === map.map[cellIndex - map.width]) {
        row.push(null);
        continue;
      }
      if (colIndex > 0 && cellPos === map.map[cellIndex - 1]) {
        row.push(null);
        continue;
      }
      row.push(tableNode.nodeAt(cellPos));
    }
    rows.push(row);
  }
  return rows;
}

/**
 * A matrix of rows back into a table. Each cell takes the type of the cell
 * that stood at its place, so a header row stays a header row.
 */
export function convertArrayOfRowsToTableNode(tableNode: PMNode, arrayOfNodes: (PMNode | null)[][]): PMNode {
  const rowsPM: PMNode[] = [];
  const map = TableMap.get(tableNode);
  for (let rowIndex = 0; rowIndex < map.height; rowIndex++) {
    const row = tableNode.child(rowIndex);
    const rowCells: PMNode[] = [];
    for (let colIndex = 0; colIndex < map.width; colIndex++) {
      const cell = arrayOfNodes[rowIndex][colIndex];
      if (!cell) continue;
      const cellPos = map.map[rowIndex * map.width + colIndex];
      const oldCell = tableNode.nodeAt(cellPos) as PMNode;
      rowCells.push(oldCell.type.createChecked({ ...cell.attrs }, cell.content, cell.marks));
    }
    rowsPM.push(row.type.createChecked(row.attrs, rowCells, row.marks));
  }
  return tableNode.type.createChecked(tableNode.attrs, rowsPM, tableNode.marks);
}

/** Move the rows at `indexesOrigin` to the place of `indexesTarget`. */
export function moveRowInArrayOfRows<T>(
  rows: T[],
  indexesOrigin: number[],
  indexesTarget: number[],
  directionOverride: -1 | 1 | 0,
): T[] {
  const direction = indexesOrigin[0] > indexesTarget[0] ? -1 : 1;
  const rowsExtracted = rows.splice(indexesOrigin[0], indexesOrigin.length);
  const positionOffset = rowsExtracted.length % 2 === 0 ? 1 : 0;
  let target: number;
  if (directionOverride === -1 && direction === 1) {
    target = indexesTarget[0] - 1;
  } else if (directionOverride === 1 && direction === -1) {
    target = indexesTarget[indexesTarget.length - 1] - positionOffset + 1;
  } else {
    target = direction === -1 ? indexesTarget[0] : indexesTarget[indexesTarget.length - 1] - positionOffset;
  }
  rows.splice(target, 0, ...rowsExtracted);
  return rows;
}

export type CellPos = { pos: number; start: number; depth: number; node: PMNode };

type CellSelectionRange = { $anchor: ResolvedPos; $head: ResolvedPos; indexes: number[] };

/** The cells of the given columns of the table around the selection. */
export function getCellsInColumn(columnIndexes: number | number[], selection: Selection): CellPos[] | undefined {
  const table = findTable(selection.$from);
  if (!table) return undefined;
  const map = TableMap.get(table.node);
  const indexes = Array.isArray(columnIndexes) ? columnIndexes : [columnIndexes];
  return indexes
    .filter((index) => index >= 0 && index <= map.width - 1)
    .flatMap((index) =>
      map.cellsInRect({ left: index, right: index + 1, top: 0, bottom: map.height }).map((nodePos) => {
        const node = table.node.nodeAt(nodePos) as PMNode;
        const pos = nodePos + table.start;
        return { pos, start: pos + 1, node, depth: table.depth + 2 };
      }),
    );
}

/** The cells of the given rows of the table around the selection. */
export function getCellsInRow(rowIndex: number | number[], selection: Selection): CellPos[] | undefined {
  const table = findTable(selection.$from);
  if (!table) return undefined;
  const map = TableMap.get(table.node);
  const indexes = Array.isArray(rowIndex) ? rowIndex : [rowIndex];
  return indexes
    .filter((index) => index >= 0 && index <= map.height - 1)
    .flatMap((index) =>
      map.cellsInRect({ left: 0, right: map.width, top: index, bottom: index + 1 }).map((nodePos) => {
        const node = table.node.nodeAt(nodePos) as PMNode;
        const pos = nodePos + table.start;
        return { pos, start: pos + 1, node, depth: table.depth + 2 };
      }),
    );
}

/** The columns a column spans with its merged cells, from Atlassian (see the header). */
export function getSelectionRangeInColumn(
  tr: Transaction,
  startColIndex: number,
  endColIndex: number = startColIndex,
): CellSelectionRange | undefined {
  let startIndex = startColIndex;
  let endIndex = endColIndex;
  for (let i = startColIndex; i >= 0; i--) {
    for (const cell of getCellsInColumn(i, tr.selection) ?? []) {
      const maybeEndIndex = cell.node.attrs.colspan + i - 1;
      if (maybeEndIndex >= startIndex) startIndex = i;
      if (maybeEndIndex > endIndex) endIndex = maybeEndIndex;
    }
  }
  for (let i = startColIndex; i <= endIndex; i++) {
    for (const cell of getCellsInColumn(i, tr.selection) ?? []) {
      const maybeEndIndex = cell.node.attrs.colspan + i - 1;
      if (cell.node.attrs.colspan > 1 && maybeEndIndex > endIndex) endIndex = maybeEndIndex;
    }
  }
  const indexes: number[] = [];
  for (let i = startIndex; i <= endIndex; i++) {
    const maybeCells = getCellsInColumn(i, tr.selection);
    if (maybeCells && maybeCells.length > 0) indexes.push(i);
  }
  startIndex = indexes[0];
  endIndex = indexes[indexes.length - 1];
  const firstSelectedColumnCells = getCellsInColumn(startIndex, tr.selection);
  const firstRowCells = getCellsInRow(0, tr.selection);
  if (!firstSelectedColumnCells || !firstRowCells) return undefined;
  const $anchor = tr.doc.resolve(firstSelectedColumnCells[firstSelectedColumnCells.length - 1].pos);
  let headCell: CellPos | undefined;
  for (let i = endIndex; i >= startIndex; i--) {
    const columnCells = getCellsInColumn(i, tr.selection);
    if (columnCells && columnCells.length > 0) {
      for (let j = firstRowCells.length - 1; j >= 0; j--) {
        if (firstRowCells[j].pos === columnCells[0].pos) {
          headCell = columnCells[0];
          break;
        }
      }
      if (headCell) break;
    }
  }
  if (!headCell) return undefined;
  return { $anchor, $head: tr.doc.resolve(headCell.pos), indexes };
}

/** The rows a row spans with its merged cells, from Atlassian (see the header). */
export function getSelectionRangeInRow(
  tr: Transaction,
  startRowIndex: number,
  endRowIndex: number = startRowIndex,
): CellSelectionRange | undefined {
  let startIndex = startRowIndex;
  let endIndex = endRowIndex;
  for (let i = startRowIndex; i >= 0; i--) {
    for (const cell of getCellsInRow(i, tr.selection) ?? []) {
      const maybeEndIndex = cell.node.attrs.rowspan + i - 1;
      if (maybeEndIndex >= startIndex) startIndex = i;
      if (maybeEndIndex > endIndex) endIndex = maybeEndIndex;
    }
  }
  for (let i = startRowIndex; i <= endIndex; i++) {
    for (const cell of getCellsInRow(i, tr.selection) ?? []) {
      const maybeEndIndex = cell.node.attrs.rowspan + i - 1;
      if (cell.node.attrs.rowspan > 1 && maybeEndIndex > endIndex) endIndex = maybeEndIndex;
    }
  }
  const indexes: number[] = [];
  for (let i = startIndex; i <= endIndex; i++) {
    const maybeCells = getCellsInRow(i, tr.selection);
    if (maybeCells && maybeCells.length > 0) indexes.push(i);
  }
  startIndex = indexes[0];
  endIndex = indexes[indexes.length - 1];
  const firstSelectedRowCells = getCellsInRow(startIndex, tr.selection);
  const firstColumnCells = getCellsInColumn(0, tr.selection);
  if (!firstSelectedRowCells || !firstColumnCells) return undefined;
  const $anchor = tr.doc.resolve(firstSelectedRowCells[firstSelectedRowCells.length - 1].pos);
  let headCell: CellPos | undefined;
  for (let i = endIndex; i >= startIndex; i--) {
    const rowCells = getCellsInRow(i, tr.selection);
    if (rowCells && rowCells.length > 0) {
      for (let j = firstColumnCells.length - 1; j >= 0; j--) {
        if (firstColumnCells[j].pos === rowCells[0].pos) {
          headCell = rowCells[0];
          break;
        }
      }
      if (headCell) break;
    }
  }
  if (!headCell) return undefined;
  return { $anchor, $head: tr.doc.resolve(headCell.pos), indexes };
}

/**
 * Put the selection inside the table at `table.pos`, when it is elsewhere.
 * The cell helpers find their table from the selection, as in Docmost.
 */
function selectInside(tr: Transaction, table: FindParentNodeResult): void {
  const current = findTable(tr.selection.$from);
  if (current && current.pos === table.pos) return;
  tr.setSelection(TextSelection.near(tr.doc.resolve(table.start + 1), 1));
}

export interface MoveParams {
  tr: Transaction;
  originIndex: number;
  targetIndex: number;
  /** Select the moved row or column after the move. */
  select: boolean;
  /** A position inside the table. */
  pos: number;
}

/** Move a column from `originIndex` to `targetIndex`. */
export function moveColumn({ tr, originIndex, targetIndex, select, pos }: MoveParams): boolean {
  const table = findTable(tr.doc.resolve(pos));
  if (!table) return false;
  selectInside(tr, table);
  const origin = getSelectionRangeInColumn(tr, originIndex)?.indexes;
  const target = getSelectionRangeInColumn(tr, targetIndex)?.indexes;
  if (!origin || !target || origin.includes(targetIndex)) return false;
  let rows = transpose(convertTableNodeToArrayOfRows(table.node));
  rows = transpose(moveRowInArrayOfRows(rows, origin, target, 0));
  const newTable = convertArrayOfRowsToTableNode(table.node, rows);
  tr.replaceWith(table.pos, table.pos + table.node.nodeSize, newTable);
  if (!select) return true;
  const map = TableMap.get(newTable);
  const $last = tr.doc.resolve(table.start + map.positionAt(map.height - 1, targetIndex, newTable));
  const $first = tr.doc.resolve(table.start + map.positionAt(0, targetIndex, newTable));
  tr.setSelection(CellSelection.colSelection($last, $first));
  return true;
}

/** Move a row from `originIndex` to `targetIndex`. */
export function moveRow({ tr, originIndex, targetIndex, select, pos }: MoveParams): boolean {
  const table = findTable(tr.doc.resolve(pos));
  if (!table) return false;
  selectInside(tr, table);
  const origin = getSelectionRangeInRow(tr, originIndex)?.indexes;
  const target = getSelectionRangeInRow(tr, targetIndex)?.indexes;
  if (!origin || !target || origin.includes(targetIndex)) return false;
  const rows = moveRowInArrayOfRows(convertTableNodeToArrayOfRows(table.node), origin, target, 0);
  const newTable = convertArrayOfRowsToTableNode(table.node, rows);
  tr.replaceWith(table.pos, table.pos + table.node.nodeSize, newTable);
  if (!select) return true;
  const map = TableMap.get(newTable);
  const $last = tr.doc.resolve(table.start + map.positionAt(targetIndex, map.width - 1, newTable));
  const $first = tr.doc.resolve(table.start + map.positionAt(targetIndex, 0, newTable));
  tr.setSelection(CellSelection.rowSelection($last, $first));
  return true;
}
