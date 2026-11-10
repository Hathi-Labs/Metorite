// ── Full width, kept in the browser (WS-61 KB-2d, `knowledge_base.md` §21.7) ─
//
// A page draws at a reading width by default. Full width draws it across the
// window. The choice is one switch for every page, kept in this browser,
// because a per-member store needs a migration (§21.7). It holds a layout
// choice only, never a row of tenant data (AGENTS.md rule 9).

import { useCallback, useSyncExternalStore } from "react";

/** The storage key. One value: "1" is on, anything else is off. */
export const FULL_WIDTH_KEY = "cc-knowledge-full-width";

/** The event that tells the other views in this tab that the choice changed. */
const CHANGED = "cc-knowledge-full-width";

/** The stored value as a switch. Pure, so the test reads it. */
export function parseFullWidth(raw: string | null): boolean {
  return raw === "1";
}

function read(): boolean {
  try {
    return parseFullWidth(window.localStorage.getItem(FULL_WIDTH_KEY));
  } catch {
    return false;
  }
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === FULL_WIDTH_KEY) onChange();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(CHANGED, onChange);
  };
}

/** The switch and its setter. The server render reads "off". */
export function useFullWidth(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(subscribe, read, () => false);
  const set = useCallback((next: boolean) => {
    try {
      if (next) window.localStorage.setItem(FULL_WIDTH_KEY, "1");
      else window.localStorage.removeItem(FULL_WIDTH_KEY);
    } catch {
      // A blocked store keeps the page at its reading width.
    }
    window.dispatchEvent(new Event(CHANGED));
  }, []);
  return [on, set];
}
