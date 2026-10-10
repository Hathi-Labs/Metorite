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
 * ## Four rules, and what enforces each
 *
 * 1. **Everything starts closed.** The store starts empty. Before this, each
 *    row held `useState(depth < 1)`, so every space opened on every mount and
 *    a row forgot its fold whenever it re-mounted.
 * 2. **Kept while you use Projects.** The store is a module, so it outlives a
 *    row, the phone drawer and a client-side navigation inside the app.
 * 3. **Forgotten when you leave.** `forgetFold()` runs when the Projects page
 *    unmounts — a route to another app. A sign-out and sign-in reload the
 *    page, which empties the module. A different member on the same tab
 *    resets it too (`dataCache.identity`).
 * 4. **Forgotten after 30 idle minutes** (`FOLD_IDLE_MS`). `resumeFold()` runs
 *    on mount and whenever the tab comes back into view.
 *
 * ⚠️ **Memory only, never `localStorage` or `sessionStorage`.** The ids are a
 * tenant's rows, and `AGENTS.md` rule 9 keeps tenant rows off the device. It
 * also makes rule 3 hold by construction: a reload cannot bring a fold back.
 */

import { useSyncExternalStore } from "react";

import { identity } from "@/lib/dataCache";

/** How long the rail keeps its folds with nothing touched. */
export const FOLD_IDLE_MS = 30 * 60 * 1000;

export interface FoldState {
  open: ReadonlySet<string>;
  /** When the member last opened, closed or picked a row. */
  touchedAt: number;
  /** Who the folds belong to (`dataCache.identity`). */
  who: string | null;
}

export const CLOSED: FoldState = { open: new Set(), touchedAt: 0, who: null };

/** The state to resume from: closed when it is stale or someone else's. */
export function resumed(
  state: FoldState,
  now: number,
  who: string | null,
  idleMs = FOLD_IDLE_MS,
): FoldState {
  if (state.open.size === 0) return state;
  if (state.who !== who || now - state.touchedAt > idleMs) return { ...CLOSED, who };
  return state;
}

/** One row opened or closed. `open` forces a side; absent flips it. */
export function toggled(
  state: FoldState,
  id: string,
  now: number,
  who: string | null,
  open?: boolean,
): FoldState {
  const next = new Set(state.open);
  if (open ?? !next.has(id)) next.add(id);
  else next.delete(id);
  return { open: next, touchedAt: now, who };
}

/** Open every id, so a selected row's ancestors show it. Same state if nothing changes. */
export function withOpen(
  state: FoldState,
  ids: readonly string[],
  now: number,
  who: string | null,
): FoldState {
  if (ids.every((id) => state.open.has(id))) return state;
  return { open: new Set([...state.open, ...ids]), touchedAt: now, who };
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
  set(toggled(resumed(state, Date.now(), identity()), id, Date.now(), identity(), open));
}

/** Open these rows, and close none. */
export function openFolds(ids: readonly string[]) {
  set(withOpen(resumed(state, Date.now(), identity()), ids, Date.now(), identity()));
}

/** Drop folds that went stale or belong to someone else. */
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
