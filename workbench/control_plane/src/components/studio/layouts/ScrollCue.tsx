"use client";

/**
 * A cue for a layout that scrolls sideways (STU-LY-4). The board of a page with more lanes than the width
 * holds hides its last lane past the edge, with nothing to say so. This draws one line and one button above
 * the content when more lies to the right, and takes them away at the end. It draws no colour of its own.
 * The pure test is `lib/studio/scrollCue.ts`.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import Button from "@/components/ui/Button";
import { hasMoreRight, stepRight } from "@/lib/studio/scrollCue";

/** The page scroller of `StudioPage` is the element that scrolls sideways. */
const SCROLLER = "[data-page-scroller]";

export default function ScrollCue({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState(false);

  const scroller = useCallback(() => box.current?.closest<HTMLElement>(SCROLLER) ?? null, []);
  const measure = useCallback(() => {
    const el = scroller();
    setMore(el ? hasMoreRight(el) : false);
  }, [scroller]);

  useEffect(() => {
    const el = scroller();
    if (!el) return;
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(el);
    if (box.current) ro?.observe(box.current);
    return () => {
      el.removeEventListener("scroll", measure);
      ro?.disconnect();
    };
  }, [scroller, measure]);

  return (
    <div ref={box} data-scroll-cue={more ? "more" : "none"} className="min-w-0">
      {more ? (
        <div className="mb-2 flex items-center justify-end gap-2">
          <span role="status" className="text-xs text-muted-foreground">More columns to the right.</span>
          <Button variant="secondary" size="sm" icon="ChevronRight"
            onClick={() => { const el = scroller(); el?.scrollBy({ left: stepRight(el.clientWidth), behavior: "smooth" }); }}>
            Scroll right
          </Button>
        </div>
      ) : null}
      {children}
    </div>
  );
}
