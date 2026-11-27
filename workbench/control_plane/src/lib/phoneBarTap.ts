"use client";

import { useEffect, useRef } from "react";

/**
 * ONE signal: a tab of the phone's bottom bar was tapped (MOB-9 fix round,
 * task 2002).
 *
 * The bar paints above every overlay. A tap on a tab is a press outside each
 * overlay under it, but a hand-rolled overlay has no outside press to hear.
 * So the shell sends this event before a tab acts, with the tab's id, and
 * every overlay that must give way listens through `useCloseOnBarTap`. The
 * tab then does its own job. `Modal`, the quick capture, the clarify modals
 * and the Focus room use it. A new hand-rolled overlay above `z-50` uses it
 * too. Fence: `e2e/phone-bar-always.spec.ts` and `phoneBarTap.test.ts`.
 */

export const BAR_TAP_EVENT = "cc-phone-bar-tap";

/** The shell calls this first in every tab's tap. `tab` is the tab's id. */
export function sendBarTap(tab: string | null): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(BAR_TAP_EVENT, { detail: { tab } }));
}

/** Run `fn` on every bar tap, with the tab's id. `active` false stops it. */
export function useBarTap(fn: (tab: string | null) => void, active = true): void {
  const ref = useRef(fn);
  useEffect(() => {
    ref.current = fn;
  });
  useEffect(() => {
    if (!active) return;
    const h = (e: Event) => ref.current((e as CustomEvent<{ tab: string | null }>).detail?.tab ?? null);
    window.addEventListener(BAR_TAP_EVENT, h);
    return () => window.removeEventListener(BAR_TAP_EVENT, h);
  }, [active]);
}

/** Close an overlay when a bar tab is tapped. */
export function useCloseOnBarTap(onClose: () => void, active = true): void {
  useBarTap(() => onClose(), active);
}
