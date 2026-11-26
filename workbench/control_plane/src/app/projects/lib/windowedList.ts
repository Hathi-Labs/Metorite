"use client";

/**
 * Draw only the rows on screen (WS-MCP AMR-4, §3.12.9). A list of more than
 * `WINDOW_ABOVE` rows uses it. Each row has a fixed height in rem, which the
 * caller states, so the range comes from plain sums. No library.
 *
 * ⚠️ The root font size is not always 16 px: compact and comfortable density
 * change it. Every px sum here uses the applied size (`rootRemPx`), or a long
 * list goes blank on a deep scroll.
 */
import { useCallback, useEffect, useState } from "react";

import { rootRemPx } from "@/lib/rootRem";

export interface WindowRange {
  start: number;
  /** One past the last row to draw. */
  end: number;
  /** The height of the rows above and below the range, in the unit of the heights. */
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

/**
 * The range of rows whose heights are in rem, for a view in px. `rootPx` is the
 * applied root font size. `before` and `after` come back in rem.
 */
export function remWindow(
  heightsRem: readonly number[],
  scrollTopPx: number,
  viewportPx: number,
  rootPx: number,
  overscan?: number,
): WindowRange {
  const px = windowRange(heightsRem.map((h) => h * rootPx), scrollTopPx, viewportPx, overscan);
  return { ...px, before: px.before / rootPx, after: px.after / rootPx };
}

/**
 * The view of a list that does not scroll by itself: the page around it does.
 * `listTop` and `boxTop` are the tops of the list and of that scroll box on
 * the screen.
 */
export function pageView(listTop: number, boxTop: number, boxHeight: number): { top: number; height: number } {
  return { top: Math.max(0, boxTop - listTop), height: boxHeight };
}

/** The view height to assume before the box has a size. */
const FIRST_VIEWPORT = 600;

/** The nearest ancestor that scrolls up and down. */
function scrollBoxOf(el: HTMLElement): HTMLElement {
  for (let at = el.parentElement; at; at = at.parentElement) {
    if (/auto|scroll/.test(window.getComputedStyle(at).overflowY)) return at;
  }
  return document.documentElement;
}

/**
 * The range of a list. Give `attach` to the `ref` of the element that scrolls.
 * With `inPage`, give it to the list itself: the page around it scrolls.
 * `before` and `after` are in rem.
 */
export function useWindowedList(
  heightsRem: readonly number[],
  { inPage = false }: { inPage?: boolean } = {},
): WindowRange & { attach: (el: HTMLElement | null) => void } {
  const [box, setBox] = useState<HTMLElement | null>(null);
  const [view, setView] = useState(() => ({ top: 0, height: FIRST_VIEWPORT, rootPx: rootRemPx() }));
  useEffect(() => {
    if (!box) return undefined;
    const outer = inPage ? scrollBoxOf(box) : box;
    const read = () => {
      const at = inPage
        ? pageView(box.getBoundingClientRect().top, outer.getBoundingClientRect().top, outer.clientHeight)
        : { top: box.scrollTop, height: box.clientHeight };
      setView({ top: at.top, height: at.height || FIRST_VIEWPORT, rootPx: rootRemPx() });
    };
    read();
    outer.addEventListener("scroll", read, { passive: true });
    const size = new ResizeObserver(read);
    size.observe(outer);
    // A change of density is an attribute of the root element.
    const density = new MutationObserver(read);
    density.observe(document.documentElement, { attributes: true });
    return () => {
      outer.removeEventListener("scroll", read);
      size.disconnect();
      density.disconnect();
    };
  }, [box, inPage]);
  const attach = useCallback((el: HTMLElement | null) => setBox(el), []);
  return { ...remWindow(heightsRem, view.top, view.height, view.rootPx), attach };
}
