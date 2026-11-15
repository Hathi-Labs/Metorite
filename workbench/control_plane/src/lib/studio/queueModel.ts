/**
 * The next-item act of a work queue (STU-LY-6b, `studio_layouts.md` L7). Pure: no React, no I/O.
 *
 * The open item of a queue has Previous and Next. Next moves to the item after the open one in the order of the
 * list, and stops at the last one (it never wraps round: a queue that ends says so). When no item is open, the
 * first one is the open item, as the detail cell reads it.
 *
 * On a phone and on a tablet the item is not beside the list (the screen is too narrow). A row of the list opens
 * the record sheet of the app frame (AF-5), which is the overlay, and its own close and "Open full page" are the
 * way back. So below `lg` the layout draws the list only.
 */

/** The id after (`delta` 1) or before (`delta` -1) the open one. `null` at an end, for an unknown id or for no item. */
export function queueStep(ids: readonly string[], open: string | null | undefined, delta: -1 | 1): string | null {
  if (ids.length === 0) return null;
  const at = open ? ids.indexOf(open) : 0;
  if (at < 0) return null;
  const to = at + delta;
  return to >= 0 && to < ids.length ? ids[to] : null;
}

/** "3 of 12" for the open item, or `null` when there is no item. An unknown id counts as the first. */
export function queuePosition(ids: readonly string[], open: string | null | undefined): string | null {
  if (ids.length === 0) return null;
  const at = open ? ids.indexOf(open) : 0;
  return `${(at < 0 ? 0 : at) + 1} of ${ids.length}`;
}
