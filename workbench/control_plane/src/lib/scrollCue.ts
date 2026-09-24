/**
 * Whether a sideways-scrolling strip hides content to the right (WS-27bm S8
 * visual review).
 *
 * The chat's `taskBoard` card draws one 180px column per lane. In the rail
 * beside the board the third column was cut at the edge, and nothing said
 * that the strip scrolls. The card now shows a cue while more columns sit to
 * the right. The decision is here, pure, so `scrollCue.test.ts` reaches it.
 */

/** Pixels of slack, so a sub-pixel remainder does not show the cue. */
const SLACK = 2;

export function hasMoreToTheRight(
  scrollWidth: number,
  clientWidth: number,
  scrollLeft: number,
): boolean {
  return scrollWidth - clientWidth - scrollLeft > SLACK;
}
