/**
 * The drag state of the cells editor (STU-AF11b-2). Pure: no React, no DOM, no I/O.
 *
 * The cells editor holds one `DragState` and calls these steps from its drag events. Each step is a
 * function of the state and, where it needs the draft, of the manifest `m`. A step never throws and
 * never changes its input. The decision of a drop is `dropVerdict` of `dropModel.ts`. This file adds
 * no second drop rule, only the state around it: what is held, what is under the pointer, and the one
 * line that says what happened.
 */
import { canDrop, dropVerdict, positionOf, type DragItem, type Position } from "./dropModel";
import type { Loc } from "./pageLayout";

/** The one line under the strip: a move that was made, or a refusal. */
export interface DragLine {
  kind: "status" | "alert";
  text: string;
}

export interface DragState {
  /** What the member holds, or null. */
  drag: DragItem | null;
  /** The row that the pointer is over, when a drop there is allowed. */
  over: Loc | null;
  /** Which half of that row. */
  position: Position | null;
  line: DragLine | null;
}

export const IDLE: DragState = { drag: null, over: null, position: null, line: null };

/** A drag starts. The last line goes, so a stale sentence never sits beside a new drag. */
export function begin(drag: DragItem): DragState {
  return { drag, over: null, position: null, line: null };
}

const sameLoc = (a: Loc | null, b: Loc | null): boolean => a !== null && b !== null && a.tabId === b.tabId && a.index === b.index;

/** True when this row is the drop target, for the highlight. */
export function isOver(s: DragState, loc: Loc): boolean {
  return sameLoc(s.over, loc);
}

export interface Hover {
  state: DragState;
  /**
   * True while something is held, so the event handler calls `preventDefault` and the browser fires the
   * drop. A refused place still takes the drop, and the drop then says why in an alert line. Only a
   * place that the model allows gets the highlight, so a place without a ring is a place that refuses.
   */
  allow: boolean;
}

/**
 * The pointer moves over a row. Nothing held: no drop and no highlight. A place that the model
 * refuses clears the highlight and still takes the drop, so the member reads the reason.
 */
export function hover(s: DragState, m: unknown, pageId: string, over: Loc, offsetY: number, height: number): Hover {
  if (!s.drag) return { state: s, allow: false };
  if (!canDrop(m, pageId, s.drag, over)) {
    return { state: s.over === null && s.position === null ? s : { ...s, over: null, position: null }, allow: true };
  }
  const position = positionOf(offsetY, height);
  if (sameLoc(s.over, over) && s.position === position) return { state: s, allow: true };
  return { state: { ...s, over: { tabId: over.tabId, index: over.index }, position }, allow: true };
}

/** The pointer left the row. The state keeps what is held. */
export function leave(s: DragState): DragState {
  return s.over === null && s.position === null ? s : { ...s, over: null, position: null };
}

export interface Dropped {
  /** The manifest after the drop. The input itself for a refusal and for a no-op. */
  next: unknown;
  state: DragState;
}

/**
 * The drop. A refusal keeps its reason as an alert line. A move keeps its sentence as a status line.
 * A no-op drop (the cell lands where it was) says nothing. The held item is let go in each case.
 */
export function drop(s: DragState, m: unknown, pageId: string, over: Loc, offsetY: number, height: number): Dropped {
  if (!s.drag) return { next: m, state: s };
  const v = dropVerdict(m, pageId, s.drag, over, positionOf(offsetY, height));
  const line: DragLine | null = v.refused ? { kind: "alert", text: v.refused } : v.said ? { kind: "status", text: v.said } : null;
  return { next: v.next, state: { drag: null, over: null, position: null, line } };
}

/** The drag ended, with or without a drop. The line stays. */
export function end(s: DragState): DragState {
  return s.drag === null && s.over === null && s.position === null ? s : { ...s, drag: null, over: null, position: null };
}

/** The slot that a drop on the empty part of a list means: after the last row, or the first slot of an empty list. */
export function listEnd(cellCount: number, tabId: string | null): { loc: Loc; offsetY: number; height: number } {
  // A pointer in the lower half of the last row (offset 1 of 1) drops after it. An empty list has row 0 and the upper half.
  return cellCount > 0
    ? { loc: { tabId, index: cellCount - 1 }, offsetY: 1, height: 1 }
    : { loc: { tabId, index: 0 }, offsetY: 0, height: 1 };
}
