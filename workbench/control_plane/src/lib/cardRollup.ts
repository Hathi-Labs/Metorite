/**
 * cardRollup — when a chat card folds to its header, and when it stays open.
 *
 * Owner request, 2026-10-10: "Automatically roll up long AGUI cards so they
 * can be closed. The accordion should close when a new card appears, but
 * remain open when needed."
 *
 * The rules, in the order they apply:
 *
 * 1. A card that WAITS on the member never folds: a pending approval, a
 *    question form, an `ask_user` prompt, a plan to edit. It draws no toggle.
 *    `lib/askPin.ts` decides what waits, and this file asks it.
 * 2. A manual open or close wins until the member toggles the card again.
 *    The choice lives for the page's life, keyed by the card's id, so a
 *    re-render or a reconnect replay keeps it. A reload forgets it.
 * 3. The NEWEST card in the transcript stays open.
 * 4. Every older LONG card folds. A short card never folds and never shows
 *    a toggle.
 *
 * "Long" is a measure, not a guess: the body is taller than
 * {@link LONG_PX}, or it lists more than {@link LONG_ROWS} rows.
 *
 * The component is `components/RollupCard.tsx`. This file is pure, so the
 * node-env vitest holds every rule. Fence: `src/lib/cardRollup.test.ts`.
 */

/** A body taller than this, in CSS pixels, is long. */
export const LONG_PX = 320;

/** A list with more rows than this is long. */
export const LONG_ROWS = 4;

/** Is the body long? A height of 0 is "not measured", never "short". */
export function isLong(heightPx: number, rows?: number): boolean {
  if (typeof rows === "number" && rows > LONG_ROWS) return true;
  return heightPx > LONG_PX;
}

export type ManualState = "open" | "closed";

export interface RollupInputs {
  /** The card waits on the member (rule 1). */
  pending: boolean;
  /** What the member chose by hand, if anything (rule 2). */
  manual?: ManualState;
  /** The card is the newest in the transcript (rule 3). */
  newest: boolean;
  /** The body is long (rule 4). */
  long: boolean;
}

/** Is the card open? The four rules, in order. */
export function rollupOpen({ pending, manual, newest, long }: RollupInputs): boolean {
  if (pending) return true;
  if (manual) return manual === "open";
  if (newest) return true;
  return !long;
}

/** Does the card draw its toggle? Only a long card that does not wait. */
export function showRollupToggle({ pending, long }: Pick<RollupInputs, "pending" | "long">): boolean {
  return long && !pending;
}

// ── The member's choices, for the page's life ──────────────────────────────

const manual = new Map<string, ManualState>();

/** What the member chose for this card, or undefined. */
export function manualStateOf(id: string): ManualState | undefined {
  return manual.get(id);
}

/** Record a toggle by hand. It beats the automatic rule until the next one. */
export function setManualState(id: string, state: ManualState): void {
  manual.set(id, state);
}

/** Tests only: forget every choice. */
export function resetManualStates(): void {
  manual.clear();
}

// ── Which card is the newest ───────────────────────────────────────────────

/**
 * The transcript's cards, so each one can ask whether it is the newest.
 *
 * Order is the DOM order of the cards, not the order they mounted in. A
 * reload mounts every card at once, and a card can mount late in an old turn
 * (a slow template). `compare` is `Node.compareDocumentPosition` in the app,
 * and the test passes its own.
 */
export class RollupRegistry<N = unknown> {
  private readonly nodes = new Map<string, N>();
  private readonly listeners = new Set<() => void>();
  private newestId: string | null = null;

  constructor(private readonly compare: (a: N, b: N) => number) {}

  /** Add or move a card. Listeners hear only when the newest changes. */
  register(id: string, node: N): void {
    this.nodes.set(id, node);
    this.recompute();
  }

  unregister(id: string): void {
    if (this.nodes.delete(id)) this.recompute();
  }

  /** The id of the newest card, or null when there is none. */
  newest(): string | null {
    return this.newestId;
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  private recompute(): void {
    let best: [string, N] | null = null;
    for (const entry of this.nodes) {
      if (!best || this.compare(entry[1], best[1]) > 0) best = entry;
    }
    const next = best ? best[0] : null;
    if (next === this.newestId) return;
    this.newestId = next;
    for (const fn of this.listeners) fn();
  }
}

/** DOM order: positive when `a` comes after `b`. */
export function domOrder(a: Node, b: Node): number {
  if (a === b) return 0;
  // DOCUMENT_POSITION_PRECEDING (2): `b` precedes `a`, so `a` is later.
  return a.compareDocumentPosition(b) & 2 ? 1 : -1;
}
