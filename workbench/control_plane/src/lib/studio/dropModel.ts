/**
 * The drop model of the cell rows in the Pages tab (STU-AF11b-1). Pure: no React, no DOM, no I/O.
 *
 * A later drag layer calls these functions. They decide where a dropped item lands and say why a
 * drop is refused. They never throw and never change their input. A refused drop and a no-op drop
 * return the SAME manifest reference, so a caller tests `next === m`.
 *
 * The edits come from `pageLayout.ts` through `layoutAct`. This file adds no second edit path.
 * A slot is a place in a list BEFORE the dragged cell leaves it. `moveCell` reads its `to.index`
 * AFTER the cell leaves, so `dropIndex` does the shift.
 */

import type { CellDef } from "./manifest";
import { addCell, moveCell, type Loc } from "./pageLayout";
import { PAGE_CAPS } from "./pageModel";
import { layoutAct } from "./tabEdit";

export type DragItem = { kind: "cell"; from: Loc } | { kind: "view"; viewId: string };
export type Position = "before" | "after";

export interface DropVerdict {
  /** The manifest after the drop. The input itself when the drop is refused or changes nothing. */
  next: unknown;
  /** The reason for a refusal, or null. A no-op drop has null. */
  refused: string | null;
  /** The sentence for the status line. It is empty for a refusal and for a no-op. */
  said: string;
}

/** The sentence of a full list. The cells editor uses the same words for its button. */
export const CELL_FULL_NOTE = `A list holds at most ${PAGE_CAPS.cellsPerList} cells.`;
const CROSS_NOTE = "A cell moves between the tabs of one page.";
const NOT_POSSIBLE = "That drop is not possible.";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isIdx = (n: unknown): n is number => typeof n === "number" && Number.isInteger(n) && n >= 0;
const isPos = (p: unknown): p is Position => p === "before" || p === "after";

/** The slot in a list, before the removal. */
export function dropSlot(over: number, position: Position): number {
  return over + (position === "after" ? 1 : 0);
}

/**
 * The `to.index` for `moveCell`. In one list, a slot after the cell shifts down by one.
 * In another tab, the slot is the index. A bad number, a bad position or a bad place gives -1.
 * The callers treat -1 as a refusal.
 */
export function dropIndex(from: Loc, over: Loc, position: Position): number {
  if (!isObj(from) || !isObj(over) || !isPos(position) || !isIdx(from.index) || !isIdx(over.index)) return -1;
  const s = dropSlot(over.index, position);
  if (from.tabId === over.tabId) return s > from.index ? s - 1 : s;
  return s;
}

/** "before" in the upper half of a row, "after" in the lower half. A bad height gives "after". */
export function positionOf(offsetY: number, height: number): Position {
  if (typeof offsetY !== "number" || typeof height !== "number" || !(height > 0) || Number.isNaN(offsetY)) return "after";
  return offsetY < height / 2 ? "before" : "after";
}

function pageOf(m: unknown, pageId: unknown): Obj | null {
  if (!isObj(m) || !Array.isArray(m.pages) || typeof pageId !== "string") return null;
  const page = m.pages.find((p) => isObj(p) && p.id === pageId);
  return isObj(page) ? page : null;
}

const hasTabs = (page: Obj) => Array.isArray(page.tabs) && page.tabs.length > 0;

/** The cell list at a place. Null for an unknown place or a page that has tabs and cells both. */
function listOf(page: Obj, tabId: unknown): CellDef[] | null {
  if (hasTabs(page) && Array.isArray(page.cells) && page.cells.length > 0) return null;
  if (tabId === null) return hasTabs(page) ? null : Array.isArray(page.cells) ? (page.cells as CellDef[]) : null;
  if (!hasTabs(page) || typeof tabId !== "string") return null;
  const tab = (page.tabs as unknown[]).find((t) => isObj(t) && t.id === tabId) as Obj | undefined;
  return tab && Array.isArray(tab.cells) ? (tab.cells as CellDef[]) : null;
}

/** The name of a list for a sentence: the title of a tab, or "the page". */
export function listName(m: unknown, pageId: string, tabId: string | null): string {
  const page = pageOf(m, pageId);
  if (!page || tabId === null || !hasTabs(page)) return "the page";
  const tab = (page.tabs as unknown[]).find((t) => isObj(t) && t.id === tabId) as Obj | undefined;
  return tab && typeof tab.title === "string" && tab.title.trim() ? tab.title : "the page";
}

const refuse = (m: unknown, why: string): DropVerdict => ({ next: m, refused: why, said: "" });

function verdict(m: unknown, pageId: string, drag: DragItem, over: Loc, position: Position): DropVerdict {
  const page = pageOf(m, pageId);
  if (!page || !isObj(over) || !isObj(drag) || !isPos(position) || !isIdx(over.index)) return refuse(m, NOT_POSSIBLE);
  if (drag.kind === "cell" && isObj(drag.from) && (drag.from.tabId === null) !== (over.tabId === null)) {
    // One side is a page list and the other is a tab. A page has one kind of list, so no move can join them.
    const own = listOf(page, drag.from.tabId);
    if (own && isIdx(drag.from.index) && drag.from.index < own.length) return refuse(m, CROSS_NOTE);
  }
  const dst = listOf(page, over.tabId);
  if (!dst) return refuse(m, NOT_POSSIBLE);
  // A row index must name a row. An empty list takes index 0 for its first slot.
  if (over.index >= Math.max(dst.length, 1)) return refuse(m, NOT_POSSIBLE);
  const where = listName(m, pageId, over.tabId);

  if (drag.kind === "view") {
    const views = (m as Obj).views;
    if (typeof drag.viewId !== "string" || !Array.isArray(views) || !views.some((v) => isObj(v) && v.id === drag.viewId)) {
      return refuse(m, NOT_POSSIBLE);
    }
    if (dst.length >= PAGE_CAPS.cellsPerList) return refuse(m, CELL_FULL_NOTE);
    const slot = dropSlot(over.index, position);
    const next = layoutAct(m, (t) => addCell(t, pageId, { tabId: over.tabId, index: slot }, drag.viewId));
    if (next === m) return refuse(m, NOT_POSSIBLE);
    return { next, refused: null, said: `Added ${drag.viewId} at position ${Math.min(slot, dst.length) + 1} in ${where}.` };
  }

  if (drag.kind !== "cell" || !isObj(drag.from)) return refuse(m, NOT_POSSIBLE);
  const from = drag.from;
  const src = listOf(page, from.tabId);
  if (!src || !isIdx(from.index) || from.index >= src.length) return refuse(m, NOT_POSSIBLE);
  const moving = src[from.index];
  const idx = dropIndex(from, over, position);
  if (idx < 0) return refuse(m, NOT_POSSIBLE);
  if (from.tabId === over.tabId) {
    const next = layoutAct(m, (t) => moveCell(t, pageId, from, { tabId: over.tabId, index: idx }));
    if (next === m) return { next: m, refused: null, said: "" };
    const at = Math.min(idx, src.length - 1);
    return { next, refused: null, said: `Moved ${moving.view} to position ${at + 1} of ${src.length} in ${where}.` };
  }
  if (dst.length >= PAGE_CAPS.cellsPerList) return refuse(m, CELL_FULL_NOTE);
  const next = layoutAct(m, (t) => moveCell(t, pageId, from, { tabId: over.tabId, index: idx }));
  if (next === m) return refuse(m, NOT_POSSIBLE);
  return { next, refused: null, said: `Moved ${moving.view} to position ${Math.min(idx, dst.length) + 1} of ${dst.length + 1} in ${where}.` };
}

/** What a drop does. It never throws. */
export function dropVerdict(m: unknown, pageId: string, drag: DragItem, over: Loc, position: Position): DropVerdict {
  try {
    return verdict(m, pageId, drag, over, position);
  } catch {
    return refuse(m, NOT_POSSIBLE);
  }
}

/** True when a drop on this row is allowed. A no-op drop is allowed, because it is harmless. */
export function canDrop(m: unknown, pageId: string, drag: DragItem, over: Loc): boolean {
  return dropVerdict(m, pageId, drag, over, "before").refused === null;
}

/** The manifest after a drop. The same reference for a refusal and for a no-op. */
export function applyDrop(m: unknown, pageId: string, drag: DragItem, over: Loc, position: Position): unknown {
  return dropVerdict(m, pageId, drag, over, position).next;
}
