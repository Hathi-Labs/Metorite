/**
 * Scroll ONE container, and never an ancestor of it.
 *
 * `Element.scrollIntoView` scrolls every scrollable ancestor of the element.
 * That includes a box with `overflow: hidden`, which a person cannot scroll but
 * a script can. So a call meant for a thread can move the app shell, and leave
 * a blank band at the bottom of the window (owner report, 2026-10-10). Use
 * these two functions instead, with the scroller you mean.
 *
 * The arithmetic is pure, and the DOM half takes a structural type, because
 * vitest here runs in node and has no DOM. Fence: `scrollWithin.test.ts`.
 */

/** The vertical extent of a box, in viewport pixels. */
export interface Span {
  top: number;
  bottom: number;
}

/**
 * The `scrollTop` that shows `target` inside `port`, with the least movement.
 * It is the `block: "nearest"` rule of `scrollIntoView`, for one box only.
 * A target taller than the port aligns its top with the top of the port.
 */
export function nearestScrollTop(scrollTop: number, port: Span, target: Span): number {
  const portHeight = port.bottom - port.top;
  const targetHeight = target.bottom - target.top;
  if (target.top < port.top) return scrollTop - (port.top - target.top);
  if (target.bottom > port.bottom) {
    if (targetHeight > portHeight) return scrollTop + (target.top - port.top);
    return scrollTop + (target.bottom - port.bottom);
  }
  return scrollTop;
}

/** The part of an element that these functions read and write. */
export interface ScrollBox {
  scrollTop: number;
  scrollHeight: number;
  clientTop: number;
  clientHeight: number;
  getBoundingClientRect(): { top: number };
  scrollTo(options: { top: number; behavior?: ScrollBehavior }): void;
}

/** The visible part of a scroller, inside its border. */
function portOf(scroller: ScrollBox): Span {
  const top = scroller.getBoundingClientRect().top + scroller.clientTop;
  return { top, bottom: top + scroller.clientHeight };
}

/**
 * Scroll `scroller` so that `target` shows, with the least movement. No other
 * box moves. Nothing happens when either one is missing.
 */
export function revealWithin(
  scroller: ScrollBox | null | undefined,
  target: { getBoundingClientRect(): Span } | null | undefined,
  behavior: ScrollBehavior = "auto",
): void {
  if (!scroller || !target) return;
  const top = nearestScrollTop(scroller.scrollTop, portOf(scroller), target.getBoundingClientRect());
  if (top !== scroller.scrollTop) scroller.scrollTo({ top, behavior });
}

/** Scroll `scroller` to its end. No other box moves. */
export function scrollToEnd(
  scroller: ScrollBox | null | undefined,
  behavior: ScrollBehavior = "auto",
): void {
  if (!scroller) return;
  scroller.scrollTo({ top: scroller.scrollHeight, behavior });
}
