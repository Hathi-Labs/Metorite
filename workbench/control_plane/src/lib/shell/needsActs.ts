/**
 * The rows an act took off the needs feed, and each row's error, for EVERY
 * reader of the feed (NS-6 slice 6a).
 *
 * My Day's "Needs you" card, the shell's bell and the sidebar's row counts
 * each read the feed through `useNeedsYou`. If each held its own "removed"
 * set, a Done in the bell would leave the card's row and the row count in
 * place until the feed read again. So they share this one store. Memory
 * only, as `ActivityControl`'s open state is.
 *
 * A shared store outlives a page, which a card's own state never did. Four
 * rules keep it from hiding a live task or showing a stale error. ⚠️ Since
 * the row counts (`useNavNeeds`, 2026-10-11), the sidebar is a reader on
 * every page. `useNeedsYou` acquires its reader even while it is disabled,
 * so that holds with the shell nav off too. So rules 2 and 3 end the
 * marks in practice, and rule 1 applies only where no sidebar is mounted.
 *
 *   1. **No reader, no memory.** When the last reader unmounts, the store
 *      empties. An act that settles while no reader is mounted writes
 *      nothing. With the shell nav on, that is a sign-out or a page with no
 *      sidebar, such as `/signin`.
 *   2. **An answer that no longer holds a row forgets it.** A removed id or
 *      an error for a row the newest answer does not hold is dropped.
 *   3. **A hold window, `ACT_HOLD_MS`.** An answer that still holds a removed
 *      row inside the window may predate the write, so the row stays hidden.
 *      An answer that holds it AFTER the window is the truth: the task is
 *      live (reopened elsewhere, or the write never landed), so the row
 *      shows. An error lasts the same window.
 *   4. **A new member forgets everything** (`onClear`, on each change of
 *      identity).
 *
 * Fence: `needsActs.test.ts`.
 */
import { onClear } from "@/lib/dataCache";

/** How long an act's mark outlives an answer that still holds its row. */
export const ACT_HOLD_MS = 30_000;

export interface ActsSnapshot {
  removed: ReadonlySet<string>;
  errors: Readonly<Record<string, string>>;
}

export const EMPTY_ACTS: ActsSnapshot = Object.freeze({
  removed: new Set<string>(),
  errors: Object.freeze({}),
});

const removedAt = new Map<string, number>();
const errorAt = new Map<string, { message: string; at: number }>();
let snapshot: ActsSnapshot = EMPTY_ACTS;
let readers = 0;
const listeners = new Set<() => void>();

function publish(): void {
  snapshot =
    removedAt.size === 0 && errorAt.size === 0
      ? EMPTY_ACTS
      : {
          removed: new Set(removedAt.keys()),
          errors: Object.fromEntries([...errorAt].map(([id, e]) => [id, e.message])),
        };
  listeners.forEach((l) => l());
}

/** The store now. The same object until something changes. */
export function actsSnapshot(): ActsSnapshot {
  return snapshot;
}

export function subscribeActs(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Take a row off (`gone`) or put it back. Ignored while no reader is mounted. */
export function removeRow(id: string, gone: boolean, now = Date.now()): void {
  if (readers === 0) return;
  if (gone) removedAt.set(id, now);
  else if (!removedAt.delete(id)) return;
  publish();
}

/**
 * When a row was taken off, or `undefined` for a row with no mark. My Day's
 * feed reads it, so the end of its own act never clears a newer mark that
 * the bell set on the same row (`expireFeedActs`).
 */
export function markedAt(id: string): number | undefined {
  return removedAt.get(id);
}

/** Show a row's error, or clear it. Ignored while no reader is mounted. */
export function setRowError(id: string, message: string | null, now = Date.now()): void {
  if (readers === 0) return;
  if (message) errorAt.set(id, { message, at: now });
  else if (!errorAt.delete(id)) return;
  publish();
}

/** Rules 2 and 3: a new answer, holding these ids, has landed. */
export function pruneActs(held: Iterable<string>, now = Date.now()): void {
  const ids = new Set(held);
  let changed = false;
  for (const [id, at] of removedAt) {
    if (!ids.has(id) || now - at >= ACT_HOLD_MS) {
      removedAt.delete(id);
      changed = true;
    }
  }
  for (const [id, e] of errorAt) {
    if (!ids.has(id) || now - e.at >= ACT_HOLD_MS) {
      errorAt.delete(id);
      changed = true;
    }
  }
  if (changed) publish();
}

/** Forget every mark. */
export function resetActs(): void {
  if (removedAt.size === 0 && errorAt.size === 0) return;
  removedAt.clear();
  errorAt.clear();
  publish();
}

/**
 * Rule 1: count a mounted reader. Returns its release. The last release
 * empties the store.
 */
export function acquireReader(): () => void {
  readers += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    readers -= 1;
    if (readers === 0) resetActs();
  };
}

// Rule 4: a new member on this browser starts with nothing hidden.
onClear(resetActs);
