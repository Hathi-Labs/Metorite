/**
 * Projects · the chart scrolls itself while you drag near its edge.
 *
 * Owner request, 2026-09-21: *"moving a task within the timeline should
 * automatically scroll left and right when we are dragging"*.
 *
 * ## What was wrong without it
 *
 * The drag never broke at the edge — `mousemove` is bound to `document`, so
 * the bar kept following the pointer past the chart. What stopped was the
 * VIEW. You dragged blind, and you ran out of screen long before you ran out
 * of calendar: at the month zoom the chart shows 24px per day, so a 900px
 * window is about five weeks. Pushing a task out two months was not one
 * gesture, it was drag, drop, scroll, pick up again.
 *
 * ## Why the numbers live here and not in the component
 *
 * `vitest.config.ts` is `environment: "node"`, so nothing in this repo can
 * test a scroll by watching one happen. What CAN be tested is the arithmetic
 * that decides it — given a pointer and a box, how fast and which way. So the
 * component keeps the `requestAnimationFrame` loop and the DOM writes, and
 * every decision is here.
 *
 * ## The one thing that is easy to get wrong
 *
 * ⚠️ **Scrolling the chart has to MOVE THE DRAG too.** The bar's position is
 * `dayStep(clientX - originX)` — a pure pointer delta. Scroll the content 100px
 * under a stationary pointer and that delta does not change, so the chart
 * slides away and the bar stays behind, stuck under the cursor's old date. The
 * caller must feed the actual scrolled distance back into the drag's origin.
 * {@link applyScroll} returns what really moved, for exactly that: at
 * `scrollLeft === 0` the request is refused by the DOM, and compensating for a
 * scroll that did not happen is the same bug mirrored.
 */

/** The live chart area, in viewport coordinates. */
export interface ScrollBox {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** Pixels per second, signed. Positive x scrolls right, positive y down. */
export interface Velocity {
  x: number;
  y: number;
}

export interface EdgeScrollOptions {
  /** How deep the trigger reaches in from each edge, in px. */
  zone?: number;
  /** Top speed, px per second, once the pointer is at the edge or past it. */
  maxX?: number;
  maxY?: number;
}

/**
 * How far in from each edge the chart starts pulling.
 *
 * 64px is a bit over two day-columns at the month zoom and under two at the
 * week zoom, which is the useful shape: wide enough to hit without aiming,
 * narrow enough that ordinary dragging in the middle of a five-week window
 * never touches it.
 */
export const EDGE_ZONE = 64;

/**
 * Top speed sideways, px/s.
 *
 * ⚠️ Read it in DAYS, because that is what the person is moving. At the month
 * zoom (24px/day) 1000px/s is about 42 days per second, so a quarter takes two
 * seconds of holding at the edge. At the week zoom (44px/day) the same number
 * is 23 days/s, which is right — a wider column means you are working in finer
 * grain and want less travel per second. The pixel constant gives that for
 * free; a days-per-second constant would need a table.
 */
export const MAX_SPEED_X = 1000;

/**
 * And vertically. Lower on purpose: rows are 40px, so 600px/s is 15 rows a
 * second. Faster than that and you cannot see what you are aiming a dependency
 * arrow at, which is the only gesture that scrolls vertically on purpose.
 */
export const MAX_SPEED_Y = 600;

/**
 * How hard to pull, from how far into the edge zone the pointer is.
 *
 * Quadratic, not linear. Linear means the chart creeps the instant you enter
 * the zone, which reads as drift rather than as a control — and the zone is
 * 64px wide, so you enter it constantly while working near an edge. Squaring
 * makes the first half of the zone almost free (at halfway, a quarter speed)
 * and keeps the top end fast.
 */
export function ramp(depth: number): number {
  const held = Math.min(Math.max(depth, 0), 1);
  return held * held;
}

/**
 * Which way the chart should move itself, and how fast.
 *
 * Zero on both axes means "not near an edge", which is the common case and
 * the one the caller can skip work for.
 *
 * ⚠️ **Past the edge counts as AT the edge, not as nothing.** The pointer
 * leaves the box constantly during a drag — over the sticky task column, over
 * the header, off the window entirely — and each of those is the clearest
 * statement yet that you want to go that way. An implementation that only
 * looked inside the box would stop scrolling exactly when the person pushed
 * hardest.
 */
export function edgeVelocity(
  pointer: { x: number; y: number },
  box: ScrollBox,
  options: EdgeScrollOptions = {},
): Velocity {
  const zone = options.zone ?? EDGE_ZONE;
  const maxX = options.maxX ?? MAX_SPEED_X;
  const maxY = options.maxY ?? MAX_SPEED_Y;

  return {
    x: axis(pointer.x, box.left, box.right, zone, maxX),
    y: axis(pointer.y, box.top, box.bottom, zone, maxY),
  };
}

function axis(
  at: number,
  low: number,
  high: number,
  zone: number,
  max: number,
): number {
  // ⚠️ A box narrower than two zones would have its two edges overlap, and the
  // near-side test would win arbitrarily. Halving keeps them meeting in the
  // middle instead — which is degenerate but not wrong, and a chart that thin
  // is a phone in landscape rather than a bug.
  const reach = Math.min(zone, (high - low) / 2);
  if (reach <= 0) return 0;
  // ⚠️ `+ 0` is not noise: it turns `-0` into `0`. Standing exactly on the
  // inner boundary of the left zone gives `ramp(0)`, and `-max * 0` is `-0` —
  // which is zero for arithmetic but not for `Object.is`, so it is a value
  // that reads as "not scrolling" in a debugger and as a distinct thing to a
  // test or to `1 / v === -Infinity`. Let one honest zero out of here.
  if (at <= low + reach) return -max * ramp((low + reach - at) / reach) + 0;
  if (at >= high - reach) return max * ramp((at - (high - reach)) / reach) + 0;
  return 0;
}

/**
 * Pixels to move this frame, from a velocity and the frame's real length.
 *
 * ⚠️ Time-based, not per-frame. A fixed step per frame ties the speed to the
 * refresh rate: the same drag travels twice as far on a 120Hz display, and
 * crawls whenever the main thread is busy — which, during a drag that
 * re-renders a Gantt, is exactly when it is busy.
 *
 * The elapsed time is clamped. A backgrounded tab or a long task can hand back
 * a gap of seconds, and multiplying a full-speed velocity by that jumps the
 * chart a third of a year in one frame.
 */
export function frameStep(velocity: number, elapsedMs: number): number {
  const held = Math.min(Math.max(elapsedMs, 0), 50);
  return (velocity * held) / 1000;
}

/**
 * Scroll an element, and report what actually moved.
 *
 * ⚠️ **The return value is the point of this function.** The caller uses it to
 * keep the dragged bar under the cursor, and a scroll the DOM refused — at
 * `scrollLeft === 0`, or at the end of the range — must report zero. Trusting
 * the requested amount instead makes the bar drift away from the pointer every
 * time somebody drags against the start of the calendar, which is the one
 * place they are most likely to be pushing hard.
 *
 * Fractional pixels are kept rather than rounded away. At 60fps a slow ramp is
 * well under one pixel per frame, and rounding each frame to zero would make
 * the whole first half of the zone dead.
 */
export function applyScroll(
  el: { scrollLeft: number; scrollTop: number },
  dx: number,
  dy: number,
): { dx: number; dy: number } {
  const beforeX = el.scrollLeft;
  const beforeY = el.scrollTop;
  if (dx !== 0) el.scrollLeft = beforeX + dx;
  if (dy !== 0) el.scrollTop = beforeY + dy;
  return { dx: el.scrollLeft - beforeX, dy: el.scrollTop - beforeY };
}
