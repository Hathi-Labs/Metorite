/**
 * One line of chips that never wraps and never shows a scrollbar, and still
 * lets every member reach every chip. The pure half of `ScrollStrip.tsx`.
 *
 * Owner report, 2026-10-06: the Email tag row ran off the right of the page,
 * and a mouse member had no way to reach the chips past the edge. The row did
 * scroll. It hid its scrollbar, so nothing said it could, and a vertical wheel
 * did nothing to it.
 *
 * The rules, for desktop and phone alike:
 *
 *   1. ONE line. The row never wraps, so it never pushes the list down.
 *   2. NO scrollbar. A fade on an edge says "more this way".
 *   3. An arrow on that edge pages by whole chips, so no chip is cut in half
 *      at the start of the view. Desktop only: a phone swipes.
 *   4. A vertical wheel over the row moves it sideways, but only while it can
 *      move. At the end of the row, the wheel scrolls the page again.
 *
 * Fence: `scrollStrip.test.ts`, and the browser test of the first consumer,
 * `e2e/email-tag-strip.spec.ts`.
 */

/** Below this many pixels, an edge counts as reached. Subpixel zoom leaves 0.5. */
export const EDGE_TOLERANCE = 1;

/** The arrow and its fade cover this much of each edge, in px. */
export const EDGE_ALLOWANCE = 36;

/** Which edges have chips hidden behind them. */
export function hiddenEdges(
  scrollLeft: number,
  clientWidth: number,
  scrollWidth: number,
): { start: boolean; end: boolean } {
  const max = Math.max(0, scrollWidth - clientWidth);
  return {
    start: scrollLeft > EDGE_TOLERANCE,
    end: scrollLeft < max - EDGE_TOLERANCE,
  };
}

/** A chip's span along the row, in the scroller's content coordinates. */
export interface Span {
  left: number;
  right: number;
}

function clamp(n: number, max: number): number {
  return Math.min(Math.max(0, n), Math.max(0, max));
}

/**
 * Where one press of an arrow should scroll to.
 *
 * Forward: the first chip cut by the right edge becomes the first chip at the
 * left. Back: the last chip cut by the left edge becomes the last chip at the
 * right. Both leave room for the arrow, which sits over the edge.
 *
 * ⚠️ A chip wider than the view would page by zero and trap the member. In that
 * case the arrow pages by most of the view instead, so every press moves.
 */
export function pageTarget(
  dir: 1 | -1,
  scrollLeft: number,
  clientWidth: number,
  scrollWidth: number,
  spans: readonly Span[],
): number {
  const max = scrollWidth - clientWidth;
  const fallback = clamp(scrollLeft + dir * clientWidth * 0.8, max);
  if (dir === 1) {
    const viewEnd = scrollLeft + clientWidth - EDGE_ALLOWANCE;
    const cut = spans.find((s) => s.right > viewEnd);
    if (!cut) return clamp(max, max);
    const target = clamp(cut.left - EDGE_ALLOWANCE, max);
    return target > scrollLeft + EDGE_TOLERANCE ? target : fallback;
  }
  const viewStart = scrollLeft + EDGE_ALLOWANCE;
  const cut = [...spans].reverse().find((s) => s.left < viewStart);
  if (!cut) return 0;
  const target = clamp(cut.right - clientWidth + EDGE_ALLOWANCE, max);
  return target < scrollLeft - EDGE_TOLERANCE ? target : fallback;
}

/**
 * How far a wheel event should move the row sideways, or 0 to let the page
 * have it.
 *
 * A trackpad's sideways swipe already scrolls the row natively, so only a
 * mostly vertical wheel is converted. At the end it is handed back, or the row
 * would swallow the page's scroll.
 */
export function wheelToRow(
  deltaX: number,
  deltaY: number,
  edges: { start: boolean; end: boolean },
): number {
  if (Math.abs(deltaY) <= Math.abs(deltaX)) return 0;
  if (deltaY > 0 && !edges.end) return 0;
  if (deltaY < 0 && !edges.start) return 0;
  return deltaY;
}

/**
 * The CSS mask that fades the edges with chips behind them. A straight edge
 * where the row is at its end, so a fade always means "more".
 */
/** A mask reads only alpha, so any opaque colour works. A token keeps the
 *  conformance fence honest: no literal colour in the tree. */
const OPAQUE = "var(--foreground)";

export function edgeMask(edges: { start: boolean; end: boolean }): string | undefined {
  if (!edges.start && !edges.end) return undefined;
  const a = edges.start ? `transparent 0, ${OPAQUE} ${EDGE_ALLOWANCE}px` : `${OPAQUE} 0`;
  const b = edges.end
    ? `${OPAQUE} calc(100% - ${EDGE_ALLOWANCE}px), transparent 100%`
    : `${OPAQUE} 100%`;
  return `linear-gradient(to right, ${a}, ${b})`;
}
