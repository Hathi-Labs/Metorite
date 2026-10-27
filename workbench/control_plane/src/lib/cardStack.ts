/**
 * cardStack — the rules of a stack of cards that wait on the member.
 *
 * Owner, 2026-10-24: "I could easily miss that there are multiple approval
 * cards", and then "an animated swipe like motion where the card moves a
 * little bit to the side in slight angle and then comes back like a spring
 * … almost like Tinder … reuse this animation for other aspects of the chat
 * application" (Projects, CRM). The look follows the common deck pattern:
 * the cards behind peek out offset, a little smaller and a little turned; a
 * drag tilts the top card in proportion to its offset; a release springs it
 * back. `components/ui/CardStack.tsx` draws it. Every app's approvals go
 * through `ConfirmationQueue`, so each stack gets it.
 *
 * Pure, so the node-env vitest holds it. Fence: `src/lib/cardStack.test.ts`.
 */

// ── The stack ───────────────────────────────────────────────────────────────
//
// "I could easily miss that there are multiple approval cards." With two or
// more cards the queue draws as a stack: a banner that counts them, the edges
// of the cards behind, a swipe between them on a touch screen, and one nudge
// that shows the swipe. Each card is still one consent: no "Approve all".

/** The px of a sideways drag that moves to the next or previous card. */
export const SWIPE_STEP_PX = 56;

/** The move that a finished drag of `dx` px asks for: +1 the next card,
 *  -1 the previous one, 0 none. A drag to the LEFT shows the next card, as
 *  a stack of paper does. The ends of the queue do not wrap. */
export function swipeStep(dx: number, at: number, total: number, step = SWIPE_STEP_PX): -1 | 0 | 1 {
  if (!Number.isFinite(dx) || total < 2) return 0;
  if (dx <= -step && at < total - 1) return 1;
  if (dx >= step && at > 0) return -1;
  return 0;
}

/** How many card edges peek out behind the shown card: at most two. */
export function stackDepth(total: number): number {
  return Math.max(0, Math.min(2, total - 1));
}

/** The banner's words for `total` waiting cards. */
export function stackBanner(total: number, noun = "approvals"): string {
  return `${total} ${noun} waiting. Swipe or use the arrows to see each one.`;
}

/** The most a dragged card turns, in degrees. Libraries cap near 15; a chat
 *  card is wide and holds text, so less reads better. */
export const MAX_TILT_DEG = 8;

/** The turn of a card dragged `dx` px: one degree per 12 px, capped. A drag
 *  to the left turns it counter-clockwise, as a card held at its foot does. */
export function dragTilt(dx: number): number {
  if (!Number.isFinite(dx)) return 0;
  const deg = dx / 12;
  return Math.max(-MAX_TILT_DEG, Math.min(MAX_TILT_DEG, Math.round(deg * 10) / 10));
}

/** How far a card behind the top one has risen while the top card is
 *  dragged, 0 to 1: it is fully up at one swipe step. */
export function revealOf(dx: number, step = SWIPE_STEP_PX): number {
  if (!Number.isFinite(dx) || step <= 0) return 0;
  return Math.min(1, Math.abs(dx) / step);
}

/** The resting pose of the card `i` places behind the top one (i = 0 is the
 *  first card behind): offset down, smaller, and turned a little, the side
 *  alternating, so the stack reads as a deck and not as a frame. */
export function edgePose(i: number): { y: number; scale: number; rotate: number } {
  return { y: (i + 1) * 9, scale: 1 - (i + 1) * 0.035, rotate: i % 2 === 0 ? -1.6 : 1.4 };
}
