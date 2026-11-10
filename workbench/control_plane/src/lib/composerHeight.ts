/**
 * How tall the chat composer is (owner request, 2026-10-09).
 *
 * The box grows with the message, up to a cap, and goes back to one line once
 * the message is sent. A long message can open a taller editor with the
 * expand control. The cap is a share of the viewport, and smaller on a phone,
 * so the on-screen keyboard and the thread above it keep room.
 *
 * Pure, so the rules are assertions, not clicks. Fence: `composerHeight.test.ts`.
 */

/** One line: the height of the send and stop buttons beside it. */
export const COMPOSER_MIN_PX = 36;
/** Below this the cap would fight a single paragraph, whatever the screen. */
const COMPOSER_FLOOR_PX = 120;

/** The phone breakpoint the rest of the shell uses (Tailwind `sm`). */
export const PHONE_MAX_WIDTH = 640;

/** The tallest the box grows by itself, or when the member expanded it. */
export function composerCap(viewportHeight: number, expanded: boolean, phone: boolean): number {
  const share = expanded ? 0.6 : phone ? 0.3 : 0.4;
  return Math.max(COMPOSER_FLOOR_PX, Math.round(viewportHeight * share));
}

/** The box's height for content of `scrollHeight`: one line at least, the cap at most. */
export function composerHeight(scrollHeight: number, cap: number): number {
  return Math.min(Math.max(scrollHeight, COMPOSER_MIN_PX), cap);
}

/**
 * Whether to offer the expand control: only when the message no longer fits,
 * or the member already expanded it (so they can shrink it back).
 */
export function offerExpand(scrollHeight: number, cap: number, expanded: boolean): boolean {
  return expanded || scrollHeight > cap;
}
