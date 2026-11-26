"use client";

/**
 * Draw only the rows on screen (WS-MCP AMR-4, §3.12.9). A list of more than
 * `WINDOW_ABOVE` rows uses it. Each row has a fixed height, which the caller
 * states, so the range comes from plain sums. No library.
 */
import { useCallback, useEffect, useState } from "react";

export interface WindowRange {
  start: number;
  /** One past the last row to draw. */
  end: number;
  /** The height of the rows above and below the range. */
  before: number;
  after: number;
}

/** The rows that touch the view, with `overscan` more on each side. */
export function windowRange(
  heights: readonly number[],
  scrollTop: number,
  viewport: number,
  overscan = 6,
): WindowRange {
  let top = 0;
  let first = 0;
  while (first < heights.length && top + heights[first] <= scrollTop) top += heights[first++];
  let last = first;
  let bottom = top;
  while (last < heights.length && bottom < scrollTop + viewport) bottom += heights[last++];
  const start = Math.max(0, first - overscan);
  const end = Math.min(heights.length, last + overscan);
  const sum = (from: number, to: number) => heights.slice(from, to).reduce((a, b) => a + b, 0);
  return { start, end, before: sum(0, start), after: sum(end, heights.length) };
}

/** The view height to assume before the box has a size. */
const FIRST_VIEWPORT = 600;

/** The range of a scroll box. Give `attach` to the `ref` of the element that scrolls. */
export function useWindowedList(heights: readonly number[]): WindowRange & { attach: (el: HTMLElement | null) => void } {
  const [box, setBox] = useState<HTMLElement | null>(null);
  const [view, setView] = useState({ top: 0, height: FIRST_VIEWPORT });
  useEffect(() => {
    if (!box) return undefined;
    const read = () => setView({ top: box.scrollTop, height: box.clientHeight || FIRST_VIEWPORT });
    read();
    box.addEventListener("scroll", read, { passive: true });
    const watch = new ResizeObserver(read);
    watch.observe(box);
    return () => {
      box.removeEventListener("scroll", read);
      watch.disconnect();
    };
  }, [box]);
  const attach = useCallback((el: HTMLElement | null) => setBox(el), []);
  return { ...windowRange(heights, view.top, view.height), attach };
}
