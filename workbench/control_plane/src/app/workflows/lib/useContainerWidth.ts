"use client";

/**
 * The width of an element in CSS px, kept current by a `ResizeObserver`
 * (row E7e). The workflow editor reads its own width with it, because the
 * shell sidebar and the assistant take a part of the window that the
 * viewport width does not show.
 *
 * Pass the element from a callback ref (`ref={setBox}` with `useState`), so
 * the hook sees an element that mounts after the first render. The width is
 * null until the element is there. The first read runs in a layout effect,
 * so the layout is right on the first paint.
 */
import { useLayoutEffect, useState } from "react";

export function useContainerWidth(el: HTMLElement | null): number | null {
  const [width, setWidth] = useState<number | null>(null);

  useLayoutEffect(() => {
    if (!el) return;
    const read = () => setWidth(Math.round(el.getBoundingClientRect().width));
    read();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(read);
    watch.observe(el);
    return () => watch.disconnect();
  }, [el]);

  return width;
}
