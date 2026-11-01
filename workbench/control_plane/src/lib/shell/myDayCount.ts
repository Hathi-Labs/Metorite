/**
 * My Day's one number (owner, 2026-11-01: one surface, one count). The page
 * publishes the count its sentence says ("20 things need you": the feed's
 * needs and the AI runs that wait), and the sidebar's My Day row shows the
 * same number. Before the page has drawn in this tab, the row shows the
 * needs view's count (`useNavNeeds`). Memory only.
 */

import { useSyncExternalStore } from "react";

import { onClear } from "@/lib/dataCache";

let _count: number | null = null;
const _listeners = new Set<() => void>();

/** My Day says this number now. */
export function publishMyDayCount(count: number): void {
  if (_count === count) return;
  _count = count;
  _listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
  };
}

/** The number My Day last said, or `null` before it drew in this tab. */
export function useMyDayCount(): number | null {
  return useSyncExternalStore(subscribe, () => _count, () => null);
}

// A new member on this browser starts with no number.
onClear(() => {
  _count = null;
  _listeners.forEach((l) => l());
});
