"use client";

/**
 * The shell's open counter (PKG-19a-2). It counts one pane open for each
 * route change, once, and not on a re-render. Off the flag, it counts
 * nothing. A route outside the starrable panes counts nothing
 * (`recordOpen`). With no signed-in account, it counts nothing.
 */
import { useEffect, useRef } from "react";

import { pinSuggestOn } from "./pinSuggestFlag";
import { recordOpen } from "./pinSuggestStore";

export function usePinOpenCount(pathname: string | null, email: string | null): void {
  const last = useRef<string | null>(null);
  useEffect(() => {
    if (!pathname || !email || !pinSuggestOn()) return;
    const key = `${email}|${pathname}`;
    if (last.current === key) return;
    last.current = key;
    recordOpen(email, pathname, Date.now());
  }, [pathname, email]);
}
