"use client";

/**
 * Warn before a member leaves a form that holds unsaved edits (STU-F2b).
 * A reload or a closed tab raises the browser's own prompt (`beforeunload`).
 * A click on an in-app link, such as the back link, asks with a confirm. The
 * click listener runs in the capture phase, so it runs before the router
 * reads the click. A refused confirm stops the click.
 */

import { useEffect, type RefObject } from "react";

export const LEAVE_QUESTION = "You have unsaved changes. Leave this page and lose them?";

/** True when a click on this link leaves the page and a modifier did not change that. */
export function clickLeaves(anchor: HTMLAnchorElement, event: MouseEvent): boolean {
  if (event.defaultPrevented || event.button !== 0) return false;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
  if (anchor.target && anchor.target !== "_self") return false;
  if (anchor.hasAttribute("download")) return false;
  const url = new URL(anchor.href, window.location.href);
  if (url.origin !== window.location.origin) return false;
  return url.pathname + url.search !== window.location.pathname + window.location.search;
}

export function useLeaveGuard(dirty: boolean, skip: RefObject<boolean>) {
  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      if (skip.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    const onClick = (e: MouseEvent) => {
      if (skip.current) return;
      const anchor = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!anchor || !clickLeaves(anchor, e)) return;
      if (!window.confirm(LEAVE_QUESTION)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty, skip]);
}
