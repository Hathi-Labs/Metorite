/**
 * The application's undo/redo stack — the arithmetic half.
 *
 * One seam for every app (owner directive, 2026-08-31: *"we made it in a way in
 * which the undo/redo system we can do across the entire application for other
 * future apps as well"*). This module knows nothing about tasks, projects or
 * the network. It moves entries between two lists, and each app supplies what
 * an entry actually does.
 *
 * ── The model: a COMMAND, not a snapshot ──────────────────────────────────
 *
 * `/tasks` already had undo, built the other way: a snapshot of the rows before
 * a change, restored wholesale, one level deep, expiring after seven seconds.
 * That works for "I just deleted the wrong thing" and cannot become a history —
 * a second snapshot has to overwrite the first, because two snapshots of
 * overlapping state cannot both be true.
 *
 * A command carries its own inverse instead, so N of them compose. The app
 * captures the previous VALUE when it makes the change, and the inverse is
 * "put it back to that". Two edits to different fields of one task undo
 * independently and in order; two edits to the SAME field undo to the value
 * each one replaced, which is what a user means by pressing undo twice.
 *
 * ── What this deliberately does NOT do ────────────────────────────────────
 *
 * **No conflict detection.** Undo re-applies a value that was true when you
 * made the change. If somebody else has edited that field since, undo wins, the
 * same way any other write would. Making it safe would need a version on every
 * write path and a merge story for the refusal — real work, and not obviously
 * an improvement, since the alternative is an undo that sometimes refuses.
 * This is what Linear and Notion do, and it is a decision rather than an
 * oversight.
 *
 * **No persistence.** The stack lives in memory for the session. A reload
 * clears it, because the inverse of a change is a closure over state that no
 * longer exists after one.
 *
 * **No cross-scope undo.** See `SCOPE` on the provider: pressing undo must
 * never revert something you cannot see.
 */

/**
 * The patch that puts `before` back, given the patch that changed it.
 *
 * The inverse of a field patch is the SAME KEYS with the values they had — so
 * this reads only the keys the change touched and leaves the rest alone. An
 * undo that sent the whole record back would revert fields the user never
 * altered, including any a colleague changed in between.
 *
 * **A missing key inverts to `null`, not to absent.** Setting a start date on a
 * task that had none must undo to *no start date*; omitting the key would leave
 * the new value in place and make undo look like it silently did nothing. This
 * is the single most common way a hand-written inverse is wrong.
 */
export function inversePatch<V>(
  before: object,
  patch: Record<string, V>,
): Record<string, V | null> {
  const source = before as Record<string, unknown>;
  const out: Record<string, V | null> = {};
  for (const key of Object.keys(patch)) {
    const had = source[key];
    out[key] = had === undefined ? null : (had as V);
  }
  return out;
}

export interface UndoEntry {
  /**
   * What this entry did, in the past tense and lower case — "moved Landing
   * page A/B". Rendered as *"Undo moved …"*, so it must read as a completed
   * action rather than a command.
   */
  label: string;
  undo: () => Promise<void> | void;
  redo: () => Promise<void> | void;
}

export interface UndoStack {
  past: readonly UndoEntry[];
  future: readonly UndoEntry[];
}

export const EMPTY_UNDO: UndoStack = { past: [], future: [] };

/**
 * How many steps back you can go.
 *
 * Bounded because every entry holds closures over the values it needs to
 * restore, and an unbounded history is an unbounded retention of rows the user
 * may have deleted. Deep enough that reaching the end is a surprise.
 */
export const UNDO_DEPTH = 50;

/**
 * Push a newly performed action.
 *
 * **The future is discarded**, which is the classic rule and the right one: you
 * undid three steps, then did something new, and the three you undid are no
 * longer reachable — they described a state that branched away. Keeping them
 * would let redo jump you onto a timeline you left.
 */
export function record(
  stack: UndoStack,
  entry: UndoEntry,
  depth = UNDO_DEPTH,
): UndoStack {
  const past = [...stack.past, entry];
  return {
    past: past.length > depth ? past.slice(past.length - depth) : past,
    future: [],
  };
}

export function canUndo(stack: UndoStack): boolean {
  return stack.past.length > 0;
}

export function canRedo(stack: UndoStack): boolean {
  return stack.future.length > 0;
}

/** What the next undo would revert, for the button's tooltip. */
export function undoLabel(stack: UndoStack): string | null {
  return stack.past.at(-1)?.label ?? null;
}

/** What the next redo would repeat. */
export function redoLabel(stack: UndoStack): string | null {
  return stack.future.at(-1)?.label ?? null;
}

/**
 * Move one step back.
 *
 * Returns the entry to run AND the stack that assumes it succeeded. The caller
 * runs the IO and restores the previous stack if it throws — this module stays
 * synchronous and pure so the ORDERING rules can be tested without a network.
 */
export function stepBack(
  stack: UndoStack,
): { stack: UndoStack; entry: UndoEntry } | null {
  const entry = stack.past.at(-1);
  if (!entry) return null;
  return {
    entry,
    stack: {
      past: stack.past.slice(0, -1),
      future: [...stack.future, entry],
    },
  };
}

/** Move one step forward. The exact mirror of `stepBack`. */
export function stepForward(
  stack: UndoStack,
): { stack: UndoStack; entry: UndoEntry } | null {
  const entry = stack.future.at(-1);
  if (!entry) return null;
  return {
    entry,
    stack: {
      past: [...stack.past, entry],
      future: stack.future.slice(0, -1),
    },
  };
}
