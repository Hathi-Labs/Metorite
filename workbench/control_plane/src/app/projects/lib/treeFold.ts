/**
 * Which rows of the Projects rail are open — remembered while you work, and
 * forgotten when you leave.
 *
 * Owner ask, 2026-10-10: *"start with all of the projects and subprojects and
 * folders compressed rather than keeping them all open. Remember what was last
 * open … while I'm navigating and using the projects app. But after an
 * appreciable amount of time, or if I've started using some other app or I've
 * logged out and logged back in, start with everything closed again."*
 *
 * (Not `src/lib/railFold.ts`, which folds the whole rail away. This one folds
 * the rows inside it.)
 *
 * ## Four rules, and what enforces each
 *
 * 1. **Everything starts closed.** The store starts empty. Before this, each
 *    row held `useState(depth < 1)`, so every space opened on every mount and
 *    a row forgot its fold whenever it re-mounted.
 * 2. **Kept while you use Projects.** The store is a module, so it outlives a
 *    row, the phone drawer and a client-side navigation inside the app.
 * 3. **Forgotten when you leave.** `forgetFold()` runs when the Projects page
 *    unmounts — a route to another app. A sign-out and a sign-in are full
 *    page loads, which empty the module. A different member resets it too
 *    (`dataCache.identity`).
 * 4. **Forgotten after 30 minutes AWAY** (`FOLD_AWAY_MS`). Away means the tab
 *    was hidden: `markHidden()` notes when, and `resumeFold()` on the tab's
 *    return compares. Time spent working on a board with the tab in view is
 *    not "away", however long, so the rail never closes under the member's
 *    hands (review of #853: an idle clock that only a fold reset closed the
 *    rail on the first chevron click after half an hour of board work).
 *
 * ⚠️ **Memory only, never `localStorage` or `sessionStorage`.** The ids are a
 * tenant's rows, and `AGENTS.md` rule 9 keeps tenant rows off the device. It
 * also makes rule 3 hold by construction: a reload cannot bring a fold back.
 */

import { useSyncExternalStore } from "react";

import { identity } from "@/lib/dataCache";

/** How long the tab may be away before the rail starts closed again. */
export const FOLD_AWAY_MS = 30 * 60 * 1000;

export interface FoldState {
  open: ReadonlySet<string>;
  /** When the tab was hidden, or null while it is in view. */
  hiddenAt: number | null;
  /** Who the folds belong to (`dataCache.identity`). */
  who: string | null;
}

export const CLOSED: FoldState = { open: new Set(), hiddenAt: null, who: null };

/** The tab went out of view at `now`. */
export function hiddenAtTime(state: FoldState, now: number): FoldState {
  return { ...state, hiddenAt: now };
}

/** The state to resume from: closed after a long absence, or for another member. */
export function resumed(
  state: FoldState,
  now: number,
  who: string | null,
  awayMs = FOLD_AWAY_MS,
): FoldState {
  if (state.open.size > 0 && state.who !== who) return { ...CLOSED, who };
  if (state.hiddenAt !== null && now - state.hiddenAt > awayMs) return { ...CLOSED, who };
  return state.hiddenAt === null ? state : { ...state, hiddenAt: null };
}

/** One row opened or closed. `open` forces a side; absent flips it. */
export function toggled(state: FoldState, id: string, who: string | null, open?: boolean): FoldState {
  const next = new Set(state.open);
  if (open ?? !next.has(id)) next.add(id);
  else next.delete(id);
  return { ...state, open: next, who };
}

/** Open every id and close none. The same state when nothing changes. */
export function withOpen(state: FoldState, ids: readonly string[], who: string | null): FoldState {
  if (ids.every((id) => state.open.has(id))) return state;
  return { ...state, open: new Set([...state.open, ...ids]), who };
}

// ── the store ───────────────────────────────────────────────────────────────

let state: FoldState = CLOSED;
const listeners = new Set<() => void>();

function set(next: FoldState) {
  if (next === state) return;
  state = next;
  for (const fn of listeners) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Open or close one row. */
export function toggleFold(id: string, open?: boolean) {
  set(toggled(state, id, identity(), open));
}

/** Open these rows, and close none. */
export function openFolds(ids: readonly string[]) {
  set(withOpen(state, ids, identity()));
}

/** The tab went out of view. */
export function markHidden() {
  set(hiddenAtTime(state, Date.now()));
}

/** The tab is in view again, or the page arrived: drop folds that went stale. */
export function resumeFold() {
  set(resumed(state, Date.now(), identity()));
}

/** Close everything — the member left the Projects app. */
export function forgetFold() {
  set(CLOSED);
}

/** Is this row open? Re-renders only the rows whose answer changed. */
export function useFoldOpen(id: string): boolean {
  return useSyncExternalStore(
    subscribe,
    () => state.open.has(id),
    () => false,
  );
}

/** For tests: the store's current state. */
export function foldSnapshot(): FoldState {
  return state;
}
