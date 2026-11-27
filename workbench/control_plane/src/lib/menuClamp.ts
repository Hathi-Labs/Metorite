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

/**
 * The tallest a menu may be: the room above the foot, less a 4px margin at each
 * end. `null` when the menu fits, so the caller adds no scroll and a flyout
 * inside the menu is not clipped. A taller menu scrolls (MOB-12 fix round 2).
 */
export function menuMaxHeight(
  height: number,
  view: { height: number },
  barTop: number | null,
): number | null {
  const room = (barTop ?? view.height) - 8;
  return height > room ? Math.max(room, 80) : null;
}

/** A ref for the menu's box, its clamped position and the height cap it needs. */
export function useClampedMenu(x: number, y: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  const [cap, setCap] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const top = phoneBarTop();
    const view = { width: window.innerWidth, height: window.innerHeight };
    // A menu taller than the room is capped, and it scrolls inside the cap.
    const max = menuMaxHeight(Math.max(r.height, el.scrollHeight), view, top);
    /* eslint-disable react-hooks/set-state-in-effect -- measured after paint, as ContextMenu always did */
    setCap(max);
    setPos(clampMenuPos({ x, y }, { width: r.width, height: max ?? r.height }, view, top));
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [x, y]);
  /** The height cap, or null when the menu fits. The caller adds it with a scroll. */
  return { ref, pos, maxHeight: cap };
}
