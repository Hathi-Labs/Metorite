"use client";

import { useEffect, type RefObject } from "react";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

/**
 * Close a popover on a click outside `root`, or on Escape. The walk is
 * `lib/outsideClick.ts`, so a portalled child that carries
 * `PREVENT_OUTSIDE_CLICK` still counts as inside.
 *
 * `onEscape`, when given, runs instead of `close` for the key, so a menu can
 * give the focus back to its trigger. A click outside leaves the focus where
 * the member clicked.
 *
 * Callers: the account menus (`components/AccountSwitcher.tsx`) and the
 * Metorite menu (`components/MetoriteFoot.tsx`).
 *
 * ⚠️ **While a switching cover is up, nothing dismisses** (review of
 * 2026-10-12). The cover (`SwitchingCover`, `data-switching-cover`) may be a
 * child of the menu, and a menu that closed on Escape took the cover with it,
 * mid-switch. So a key or a press during a cover leaves every menu open.
 */
export const COVER_SELECTOR = "[data-switching-cover]";

function coverUp(): boolean {
  return typeof document !== "undefined" && document.querySelector(COVER_SELECTOR) !== null;
}

/** The hook itself. The note above says what it closes and when. */
export function useDismiss(
  open: boolean,
  close: () => void,
  root: RefObject<HTMLElement | null>,
  onEscape?: () => void,
): void {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (coverUp()) return;
      if (shouldDismiss(e.target as Element | null, domClickWalk(root.current))) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !coverUp()) (onEscape ?? close)();
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, close, root, onEscape]);
}
