"use client";

/**
 * ShowMore — a long body in a chat card, cut with a fade and "Show all".
 *
 * Owner report, 2026-10-23. A card body that scrolled in its own box sat
 * inside the thread, which also scrolls. On a phone, a swipe moved the
 * wrong one, and the end of an email hid with no sign. A chat card never
 * holds a scroll box (`projects_ai_chat.md` §24.10, rule C3). A long body
 * shows its first part, and "Show all" opens the rest in place, so the
 * thread is the one thing that scrolls.
 *
 * A body that fits draws no fade and no control. Fence:
 * `src/lib/chatCardScroll.test.ts`.
 */

import { useLayoutEffect, useRef, useState } from "react";

import Button from "@/components/ui/Button";

/** The cut, in px. About fourteen lines of `text-xs`. */
export const SHOW_MORE_CAP = 288;

export default function ShowMore({
  children,
  cap = SHOW_MORE_CAP,
  className = "",
}: {
  children: React.ReactNode;
  cap?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [long, setLong] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setLong(el.scrollHeight > cap + 1);
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(el);
    return () => ro?.disconnect();
  }, [cap]);
  const cut = long && !open;
  return (
    <div data-show-more={cut ? "cut" : long ? "open" : "fits"}>
      <div
        ref={ref}
        className={`overflow-hidden ${className}`}
        style={cut ? {
          maxHeight: cap,
          maskImage: "linear-gradient(to bottom, black calc(100% - 48px), transparent)",
          WebkitMaskImage: "linear-gradient(to bottom, black calc(100% - 48px), transparent)",
        } : undefined}
      >
        {children}
      </div>
      {long && (
        <Button
          type="button"
          variant="text"
          size="none"
          layout="inline"
          icon={open ? "ChevronUp" : "ChevronDown"}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="mt-1 text-[11px]"
        >
          {open ? "Show less" : "Show all"}
        </Button>
      )}
    </div>
  );
}
