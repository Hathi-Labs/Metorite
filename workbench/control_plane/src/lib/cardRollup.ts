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
  /**
   * The focus is inside the card, and this is whether the card was open when
   * it came in. An automatic fold would hide the focused control, and a
   * keyboard member would lose their place (review round 1). So the card
   * keeps that state until the focus leaves, and only a toggle by hand
   * changes it.
   *
   * ⚠️ It HOLDS the state. It does not open the card. Focus on a shut card's
   * header used to open it (owner feedback round, 2026-10-10): a mouse press
   * focuses the header before its click, the card opened under the pointer,
   * and the click then landed on an open card and shut it, or landed nowhere.
   */
  held?: boolean;
}

/** Is the card open? The four rules, in order. */
export function rollupOpen({ pending, manual, newest, long, held }: RollupInputs): boolean {
  if (pending) return true;
  if (manual) return manual === "open";
  if (held !== undefined) return held;
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
 * `compare` decides the order, and the app passes {@link turnThenArrival}:
 * turns in DOM order, and the cards of one turn in the order they arrived.
 * DOM order alone was wrong inside a turn (review round 1): a turn draws its
 * generative-UI cards ABOVE its receipts, so a table that came after a write
 * lost "newest" to the receipt and folded before the member saw it. The
 * test passes its own `compare`.
 */
export interface RollupEntry<N> {
  node: N;
  /** The order the card registered in. A reload registers in tree order. */
  seq: number;
}

export class RollupRegistry<N = unknown> {
  private readonly nodes = new Map<string, RollupEntry<N>>();
  private readonly listeners = new Set<() => void>();
  private newestId: string | null = null;
  private seq = 0;

  constructor(private readonly compare: (a: RollupEntry<N>, b: RollupEntry<N>) => number) {}

  /** Add a card. Listeners hear only when the newest changes. */
  register(id: string, node: N): void {
    this.nodes.set(id, { node, seq: this.seq++ });
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
    let best: [string, RollupEntry<N>] | null = null;
    for (const entry of this.nodes) {
      if (!best || this.compare(entry[1], best[1]) > 0) best = entry;
    }
    const next = best ? best[0] : null;
    if (next === this.newestId) return;
    this.newestId = next;
    for (const fn of this.listeners) fn();
  }
}

/** The attribute on a turn's wrapper in the transcript (`AgentChat`). */
export const TURN_ATTR = "data-rollup-turn";

/** Turns in DOM order, and the cards of one turn by arrival. */
export function turnThenArrival(a: RollupEntry<Element>, b: RollupEntry<Element>): number {
  const ta = a.node.closest(`[${TURN_ATTR}]`);
  if (ta && ta === b.node.closest(`[${TURN_ATTR}]`)) return a.seq - b.seq;
  return domOrder(a.node, b.node);
}

/** DOM order: positive when `a` comes after `b`. */
export function domOrder(a: Node, b: Node): number {
  if (a === b) return 0;
  // DOCUMENT_POSITION_PRECEDING (2): `b` precedes `a`, so `a` is later.
  return a.compareDocumentPosition(b) & 2 ? 1 : -1;
}
