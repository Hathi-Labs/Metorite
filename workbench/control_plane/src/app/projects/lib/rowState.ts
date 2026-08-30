/**
 * Projects · per-row pending and per-row error (WS-27bd item 2).
 *
 * The usual shape is one `isPending` boolean and one `error` string for a whole
 * list. It has two failure modes and they are both silent:
 *
 *   • Three concurrent operations show ONE spinner and disable every row, so a
 *     list where two of three succeeded looks like a list where nothing did.
 *   • One failure is attributed to the LIST, so the reader has to guess which
 *     row it was about — and after a second operation lands, the message is
 *     about a row that is no longer even on screen.
 *
 * So the state is a `Set` of in-flight ids and a `Map` of id → message, and it
 * lives here rather than inside the component: `vitest.config.ts` is a node
 * environment collecting `src/**\/*.test.ts` only, so a reducer in a `.tsx`
 * file is a reducer nothing can test. Fence: `rowState.test.ts`.
 *
 * ## Two rules that are easy to get backwards
 *
 * 1. **A retry does not clear the previous error on entry.** It clears it on
 *    SUCCESS. Blanking the message the moment the retry starts hides the
 *    failure at exactly the moment somebody is reading it.
 * 2. **A row that has gone away takes its state with it.** `prune` is what
 *    stops a spinner or a message outliving the row it was attributed to;
 *    without it an unlink that fails, then succeeds on retry, leaves an error
 *    keyed to a link id nothing renders — invisible, and permanent.
 */

export interface RowState {
  /** Ids with an operation in flight. */
  pending: ReadonlySet<string>;
  /** Ids whose last operation failed, and what it said. */
  errors: ReadonlyMap<string, string>;
}

/** No row is doing anything. The identity a component initialises with. */
export const NO_ROWS: RowState = { pending: new Set(), errors: new Map() };

export type RowEvent =
  | { kind: "start"; id: string }
  | { kind: "settled"; id: string }
  | { kind: "failed"; id: string; message: string }
  | { kind: "dismiss"; id: string }
  /** Keep only these ids — everything else has left the list. */
  | { kind: "prune"; ids: readonly string[] };

const withOut = <T>(set: ReadonlySet<T>, id: T): Set<T> => {
  const next = new Set(set);
  next.delete(id);
  return next;
};

/**
 * One event against the row state.
 *
 * Returns the SAME object when nothing changed, so a component can dispatch
 * `prune` from an effect on every load without re-rendering itself in a loop.
 */
export function rowsReducer(state: RowState, event: RowEvent): RowState {
  switch (event.kind) {
    case "start": {
      if (state.pending.has(event.id)) return state;
      // Note what is NOT here: the row's error. See rule 1 above.
      return { ...state, pending: new Set(state.pending).add(event.id) };
    }
    case "settled": {
      if (!state.pending.has(event.id) && !state.errors.has(event.id)) return state;
      const errors = new Map(state.errors);
      errors.delete(event.id);
      return { pending: withOut(state.pending, event.id), errors };
    }
    case "failed": {
      if (
        !state.pending.has(event.id) &&
        state.errors.get(event.id) === event.message
      )
        return state;
      return {
        pending: withOut(state.pending, event.id),
        errors: new Map(state.errors).set(event.id, event.message),
      };
    }
    case "dismiss": {
      if (!state.errors.has(event.id)) return state;
      const errors = new Map(state.errors);
      errors.delete(event.id);
      return { ...state, errors };
    }
    case "prune": {
      const keep = new Set(event.ids);
      const pending = [...state.pending].filter((id) => keep.has(id));
      const errors = [...state.errors].filter(([id]) => keep.has(id));
      if (pending.length === state.pending.size && errors.length === state.errors.size)
        return state;
      return { pending: new Set(pending), errors: new Map(errors) };
    }
  }
}

/** Is this row busy? */
export function isPending(state: RowState, id: string): boolean {
  return state.pending.has(id);
}

/** What this row's last operation said when it failed, or null. */
export function errorFor(state: RowState, id: string): string | null {
  return state.errors.get(id) ?? null;
}

/** Is anything at all in flight? For a list-level affordance, never a row's. */
export function anyPending(state: RowState): boolean {
  return state.pending.size > 0;
}
