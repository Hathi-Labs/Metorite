// ── The shared filter model (STU-AF4, task 3152) ──────────────────────────
//
// Contract. This module is the pure, app-free half of the CRM filter bar:
// the search box state machine, its timer, and the setters of a view that
// holds `custom` and `ranges`. It imports no app code and draws nothing.
// Any app filter bar may import it. The CRM re-exports it from
// `app/crm/lib/filterBar.ts`, which keeps only the CRM rules (owners,
// sources, field definitions, mirror pruning). Fence: `filterModel.test.ts`.

import { coalesce } from "@/lib/useCachedResource";

/** The option value that means no filter. */
export const ANY = "";

/**
 * The search box's text, and what it knows about the URL (CRM-X1).
 *
 * `seen` is the last `?q=` the box reconciled. `mine` is the last query the
 * box wrote itself, as the URL holds it, or null.
 */
export type SearchBoxState = { draft: string; seen: string; mine: string | null };

export function searchBoxStart(value: string): SearchBoxState {
  return { draft: value, seen: value, mine: null };
}

/**
 * The URL's `?q=` is now `value`. A value that the box did not write (Back,
 * Clear, a saved view) replaces the text. The box's own write does not, so a
 * key typed while the URL catches up is never lost.
 */
export function searchBoxSync(state: SearchBoxState, value: string): SearchBoxState {
  if (value === state.seen) return state;
  return { draft: value === state.mine ? state.draft : value, seen: value, mine: null };
}

/**
 * The box wrote `q`. It remembers the write trimmed, because the URL holds it
 * trimmed (`serializeView`). Untrimmed, a pause after a space read as a write
 * from outside and ate the space: "printer order" became "printerorder".
 */
export function searchBoxWrote(state: SearchBoxState, q: string): SearchBoxState {
  return { ...state, mine: q.trim() };
}

/** Does writing `q` change the URL, which holds `current`? */
export function searchChanges(q: string, current: string): boolean {
  return q.trim() !== current.trim();
}

/** How long the search box waits after the last key before it writes `?q=`. */
export const SEARCH_DELAY_MS = 300;

/**
 * The search box's timer (CRM-X1). Each key calls `type`, and `commit` runs
 * once, with the last text, `ms` after the last key. It is the shared
 * trailing coalescer, so the box adds no second debounce.
 */
export function searchCommitter(
  commit: (q: string) => void,
  ms = SEARCH_DELAY_MS,
): { type: (q: string) => void; cancel: () => void } {
  let latest = "";
  const timer = coalesce(() => commit(latest), ms);
  return {
    type(q: string) {
      latest = q;
      timer.trigger();
    },
    cancel: timer.cancel,
  };
}

/** Choose one option of a picklist, or none. Other fields stay as they are. */
export function setCustom<V extends { custom: Record<string, string[]> }>(
  view: V,
  key: string,
  value: string,
): V {
  const custom = { ...view.custom };
  if (value) custom[key] = [value];
  else delete custom[key];
  return { ...view, custom };
}

/** Set or clear one end of a date range. An empty or invalid value clears it. */
export function setRange<K extends string, V extends { ranges: Partial<Record<K, string>> }>(
  view: V,
  key: K,
  value: string,
): V {
  const ranges = { ...view.ranges };
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) ranges[key] = value;
  else delete ranges[key];
  return { ...view, ranges };
}
