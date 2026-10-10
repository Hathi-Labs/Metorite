/**
 * stickToBottom — a transcript that is read at its bottom stays at its bottom.
 *
 * Owner request, 2026-10-10: when an approval card arrives and the member is
 * already at the bottom, the card appears in view, with no jump and no pin.
 * When the member has scrolled up, nothing moves and the pin
 * (`components/AskPin.tsx`) offers "Show".
 *
 * The defect this replaces, measured in a browser on 2026-10-10: the chat
 * followed the bottom only when `messages` changed. An approval card is not
 * a message. It comes from the confirmation queue, so it drew BELOW the fold
 * of a member who sat at the bottom, and the pin then said "Waiting for your
 * approval · Show" for a card the member had never scrolled away from.
 *
 * The rule: whenever the transcript's content or its viewport changes size,
 * a member who was at the bottom is put back at the bottom, before the frame
 * paints (a `ResizeObserver` callback runs before paint, so there is no
 * jolt). The one exception is a fold or an open by hand: the member is
 * looking at that card, so the transcript keeps its place and only learns
 * where it now is.
 *
 * Pure apart from {@link attachStickToBottom}, which takes its observer as
 * an argument so the node-env vitest can drive it. Fence:
 * `src/lib/stickToBottom.test.ts`.
 */

/** Closer to the bottom than this, in CSS pixels, counts as at the bottom. */
export const NEAR_BOTTOM_PX = 80;

/** How long after a toggle by hand a resize is the toggle's own. */
export const MANUAL_TOGGLE_QUIET_MS = 700;

export interface ScrollBox {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}

export function distanceFromBottom(el: ScrollBox): number {
  return el.scrollHeight - el.scrollTop - el.clientHeight;
}

export function isNearBottom(el: ScrollBox): boolean {
  return distanceFromBottom(el) < NEAR_BOTTOM_PX;
}

/**
 * Is the member still at the bottom after a scroll event?
 *
 * ⚠️ Only a scroll UP can take the member away from the bottom. A browser
 * fires the scroll event a frame late, after the layout of that frame, and
 * before its `ResizeObserver` callbacks. So the event of our own snap to the
 * bottom arrives when a card has already grown the content under it, and it
 * reads as "far from the bottom". Measured in the browser on 2026-10-10:
 * that late event cleared the flag, and the observer then let the approval
 * card land below the fold. A scroll that moved down, or did not move, keeps
 * what the member had.
 */
export function nearAfterScroll(wasNear: boolean, prevScrollTop: number, el: ScrollBox): boolean {
  if (el.scrollTop < prevScrollTop - 1) return isNearBottom(el);
  return wasNear || isNearBottom(el);
}

/** Does a resize put the member back at the bottom? */
export function pinOnResize(wasNearBottom: boolean, msSinceManualToggle: number): boolean {
  return wasNearBottom && msSinceManualToggle >= MANUAL_TOGGLE_QUIET_MS;
}

type Observer = { observe: (el: Element) => void; disconnect: () => void };
type ObserverCtor = new (cb: () => void) => Observer;

export interface StickOptions {
  /** Was the member at the bottom, as of the last scroll? */
  getNearBottom: () => boolean;
  setNearBottom: (near: boolean) => void;
  /** When the member last folded or opened a card, on `now`'s clock. */
  lastManualToggleAt: () => number;
  now?: () => number;
  /** The observer class. `ResizeObserver` in the page. */
  Observer?: ObserverCtor;
}

/**
 * Watch the transcript's viewport and its content, and keep a member who
 * reads the bottom at the bottom. Returns the cleanup.
 */
export function attachStickToBottom(
  thread: HTMLElement,
  content: Element,
  opts: StickOptions,
): () => void {
  const Ctor =
    opts.Observer ??
    (typeof ResizeObserver !== "undefined" ? (ResizeObserver as unknown as ObserverCtor) : undefined);
  if (!Ctor) return () => {};
  const now = opts.now ?? (() => performance.now());
  const ro = new Ctor(() => {
    if (pinOnResize(opts.getNearBottom(), now() - opts.lastManualToggleAt())) {
      thread.scrollTop = thread.scrollHeight;
      opts.setNearBottom(true);
    } else {
      opts.setNearBottom(isNearBottom(thread));
    }
  });
  ro.observe(thread);
  ro.observe(content);
  return () => ro.disconnect();
}
