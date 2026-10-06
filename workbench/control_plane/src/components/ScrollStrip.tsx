"use client";

/**
 * ScrollStrip — one line of chips that never wraps, never shows a scrollbar,
 * and still lets every member reach every chip.
 *
 * The rules and the arithmetic are in `src/lib/scrollStrip.ts`. This file is
 * the DOM half:
 *
 *   • a fade on each edge that has chips behind it;
 *   • an arrow on that edge, which pages by whole chips (desktop only — a
 *     coarse pointer swipes, and an arrow there only takes width);
 *   • a vertical wheel over the row moves it sideways, until the end;
 *   • the chip named by `revealKey`, or the chip that takes focus, scrolls
 *     clear of the arrows.
 *
 * Children are the chips, one element each. The strip measures them with
 * `offsetLeft`, so the scroller is `relative` and is their offset parent.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import Button from "@/components/ui/Button";
import {
  EDGE_ALLOWANCE,
  edgeMask,
  hiddenEdges,
  pageTarget,
  wheelToRow,
  type Span,
} from "@/lib/scrollStrip";

const NONE = { start: false, end: false };

function motion(): ScrollBehavior {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
  } catch {
    return "auto";
  }
}

export default function ScrollStrip({
  children,
  label,
  revealKey,
  className = "",
}: {
  children: ReactNode;
  /** Names the row for a screen reader, such as "Quick filters". */
  label: string;
  /**
   * Change this to bring the selected chip (`aria-pressed="true"`) into view.
   * A chip turned on from somewhere else, such as a search pill, must not stay
   * hidden behind the edge.
   */
  revealKey?: string;
  /** Layout only: padding and gap of the row. */
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState(NONE);

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const next = hiddenEdges(el.scrollLeft, el.clientWidth, el.scrollWidth);
    setEdges((prev) =>
      prev.start === next.start && prev.end === next.end ? prev : next,
    );
  }, []);

  // Scroll, a resize of the row, and a chip arriving or leaving all move the
  // edges. A ResizeObserver on the row alone misses a new chip, so the chips'
  // own list is watched too.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    const mo = new MutationObserver(measure);
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      el.removeEventListener("scroll", measure);
      ro.disconnect();
      mo.disconnect();
    };
  }, [measure]);

  // ⚠️ Not React's onWheel. React registers wheel as passive, and a passive
  // listener cannot call preventDefault, so the page would scroll as well.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const now = hiddenEdges(el.scrollLeft, el.clientWidth, el.scrollWidth);
      const dx = wheelToRow(e.deltaX, e.deltaY, now);
      if (dx === 0) return;
      e.preventDefault();
      el.scrollLeft += dx;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const spans = (): Span[] => {
    const el = ref.current;
    if (!el) return [];
    return Array.from(el.children).map((c) => {
      const h = c as HTMLElement;
      return { left: h.offsetLeft, right: h.offsetLeft + h.offsetWidth };
    });
  };

  const page = (dir: 1 | -1) => {
    const el = ref.current;
    if (!el) return;
    const left = pageTarget(
      dir,
      el.scrollLeft,
      el.clientWidth,
      el.scrollWidth,
      spans(),
    );
    el.scrollTo({ left, behavior: motion() });
  };

  /** Scroll `chip` clear of the arrows, if it is not already. */
  const reveal = useCallback((chip: HTMLElement) => {
    const el = ref.current;
    if (!el || chip.parentElement !== el) return;
    const left = chip.offsetLeft;
    const right = left + chip.offsetWidth;
    const view = el.scrollLeft;
    if (left < view + EDGE_ALLOWANCE) {
      el.scrollTo({
        left: Math.max(0, left - EDGE_ALLOWANCE),
        behavior: motion(),
      });
    } else if (right > view + el.clientWidth - EDGE_ALLOWANCE) {
      el.scrollTo({
        left: right - el.clientWidth + EDGE_ALLOWANCE,
        behavior: motion(),
      });
    }
  }, []);

  useEffect(() => {
    if (revealKey === undefined) return;
    const chip = ref.current?.querySelector<HTMLElement>(
      ':scope > [aria-pressed="true"]',
    );
    if (chip) reveal(chip);
  }, [revealKey, reveal]);

  const mask = edgeMask(edges);
  return (
    <div className="relative flex min-w-0 flex-1 items-center">
      <div
        ref={ref}
        role="group"
        aria-label={label}
        onFocus={(e) => reveal(e.target as HTMLElement)}
        style={mask ? { maskImage: mask, WebkitMaskImage: mask } : undefined}
        className={`scrollbar-hide relative flex min-w-0 flex-1 items-center overflow-x-auto overscroll-x-contain ${className}`}
      >
        {children}
      </div>
      {/* The arrows are a mouse aid. A keyboard member tabs along the chips,
          and each chip scrolls itself clear on focus, so the arrows stay out
          of the tab order. */}
      {/* ⚠️ The wrapper is positioned, not the Button. `.cc-control` sets
          `position: relative`, which beats Tailwind's `absolute`, and both
          arrows then sat side by side at the right end, in the flow. */}
      {edges.start && (
        <span className="absolute inset-y-0 left-0 flex items-center pointer-coarse:hidden">
          {/* An opaque disc, so the chips under the fade do not show through. */}
          <span className="rounded-full bg-card">
            <Button
              variant="secondary"
              size="icon-xs"
              radius="keep"
              icon="ChevronLeft"
              tabIndex={-1}
              aria-label={`${label}: show earlier`}
              title="Show earlier"
              data-strip-arrow="start"
              onClick={() => page(-1)}
              className="rounded-full shadow-sm"
            />
          </span>
        </span>
      )}
      {edges.end && (
        <span className="absolute inset-y-0 right-0 flex items-center pointer-coarse:hidden">
          {/* An opaque disc, so the chips under the fade do not show through. */}
          <span className="rounded-full bg-card">
            <Button
              variant="secondary"
              size="icon-xs"
              radius="keep"
              icon="ChevronRight"
              tabIndex={-1}
              aria-label={`${label}: show more`}
              title="Show more"
              data-strip-arrow="end"
              onClick={() => page(1)}
              className="rounded-full shadow-sm"
            />
          </span>
        </span>
      )}
    </div>
  );
}
