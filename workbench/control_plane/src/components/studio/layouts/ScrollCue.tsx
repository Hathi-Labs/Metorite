"use client";

/**
 * A cue for a board that scrolls sideways (STU-LY-4). A board with more lanes than the width holds hides
 * its last lane past the edge, with nothing to say so. This draws one line and one button above the board
 * when more lies to the right, and takes them away at the end. It draws no colour of its own.
 * The board scrolls itself (`Board` with `scroll="own"`), so the cue reads that element, not the page.
 * The pure test is the shared one, `lib/scrollCue.ts`.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import Button from "@/components/ui/Button";
import { hasMoreToTheRight, stepRight } from "@/lib/scrollCue";

/** The element of `StudioBoard` that scrolls sideways. `LayoutBody.test.ts` holds the two together. */
export const BOARD_SCROLLER = '[aria-label="Board"]';

const motion = (): ScrollBehavior =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";

export default function ScrollCue({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState(false);

  const scroller = useCallback(() => box.current?.querySelector<HTMLElement>(BOARD_SCROLLER) ?? null, []);

  useEffect(() => {
    const root = box.current;
    if (!root) return;
    const measure = () => {
      const el = scroller();
      setMore(el ? hasMoreToTheRight(el.scrollWidth, el.clientWidth, el.scrollLeft) : false);
    };
    // A scroll does not bubble, so the box listens in the capture phase. The board mounts after its rows
    // load, and a ResizeObserver reports once when it starts, so both observers drive the first measure.
    root.addEventListener("scroll", measure, { capture: true, passive: true });
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(root);
    const mo = typeof MutationObserver === "undefined" ? null : new MutationObserver(measure);
    mo?.observe(root, { childList: true, subtree: true });
    return () => {
      root.removeEventListener("scroll", measure, { capture: true });
      ro?.disconnect();
      mo?.disconnect();
    };
  }, [scroller]);

  return (
    <div ref={box} data-scroll-cue={more ? "more" : "none"} className="min-w-0">
      {more ? (
        <div className="mb-2 flex items-center justify-end gap-2">
          <span role="status" className="text-xs text-muted-foreground">More columns to the right.</span>
          <Button variant="secondary" size="sm" icon="ChevronRight"
            onClick={() => { const el = scroller(); el?.scrollBy({ left: stepRight(el.clientWidth), behavior: motion() }); }}>
            Scroll right
          </Button>
        </div>
      ) : null}
      {children}
    </div>
  );
}
