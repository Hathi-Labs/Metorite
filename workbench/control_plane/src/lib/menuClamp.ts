"use client";

import { useLayoutEffect, useRef, useState } from "react";

import { phoneBarTop } from "@/components/ui/AnchoredPanel";

/**
 * Where a menu may sit so it stays on screen (MOB-9 fix round, task 2002).
 * The foot is the window's, or the top edge of the phone's bottom bar when one
 * shows, because the bar paints above every menu and a tap under it hits a tab.
 * Pure, so a test can read the math.
 */
export function clampMenuPos(
  pos: { x: number; y: number },
  size: { width: number; height: number },
  view: { width: number; height: number },
  barTop: number | null,
): { x: number; y: number } {
  const floor = barTop ?? view.height;
  let { x, y } = pos;
  if (x + size.width > view.width) x = Math.max(4, view.width - size.width - 4);
  if (y + size.height > floor) y = Math.max(4, floor - size.height - 4);
  return { x, y };
}

/** A ref for the menu's box, and its clamped position. */
export function useClampedMenu(x: number, y: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    /* eslint-disable react-hooks/set-state-in-effect -- measured after paint, as ContextMenu always did */
    setPos(
      clampMenuPos(
        { x, y },
        { width: r.width, height: r.height },
        { width: window.innerWidth, height: window.innerHeight },
        phoneBarTop(),
      ),
    );
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [x, y]);
  return { ref, pos };
}
