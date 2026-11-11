"use client";

/**
 * The open tab of the task card, mirrored in `?ttab=` (ORC-3, D181).
 *
 * React state is the truth while a card is open. The address is a mirror, so
 * a reload or a copied address opens the same tab.
 *
 * ⚠️ **`history.replaceState`, never `router.replace` and never a push.**
 * A tab is not a place: Back must close the card, not walk the tabs. And
 * `router.replace` is a navigation that re-reads the route, when the change
 * is one key of the query string. Next.js syncs `useSearchParams` with
 * `replaceState`, so a page that reads the query still sees the key.
 *
 * ⚠️ **The key leaves when the LAST card closes, one tick later.** Until
 * ORC-2, Projects mounts the panel in two places (side and full), so expand
 * unmounts one body and mounts the other in the same commit. A body that
 * took the key out at once would send the expanded card back to Overview.
 * So each body counts itself, and the cleanup waits a tick: a body that
 * mounted again keeps the key, and a card that really closed drops it.
 * The check also needs the same path, so a move to another app never
 * rewrites that app's address.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { type TaskTab, searchWithTab, tabOnMount } from "../lib/taskTabs";

/** The task bodies on screen now. Module state, because two mounts share it. */
let mounted = 0;

function replaceSearch(search: string): void {
  const { pathname, hash } = window.location;
  if (search === window.location.search) return;
  window.history.replaceState(null, "", `${pathname}${search}${hash}`);
}

export function useTaskTab(): [TaskTab, (tab: TaskTab) => void] {
  // A lazy read of the address: a link that names Discussion opens on
  // Discussion, with no flash of Overview. The body only renders once a task
  // has loaded in the browser, so the server never draws it. `mounted` is
  // read in the render, before this commit's cleanup, so expand still sees
  // the body that it replaces.
  const [tab, setTabState] = useState<TaskTab>(() =>
    typeof window === "undefined" ? "overview" : tabOnMount(window.location.search, mounted),
  );
  const opened = useRef(tab);

  useEffect(() => {
    mounted += 1;
    const path = window.location.pathname;
    // The address follows the tab the card opened on. A stale key (a reload
    // with no card open) leaves here.
    replaceSearch(searchWithTab(window.location.search, opened.current));
    return () => {
      mounted -= 1;
      window.setTimeout(() => {
        if (mounted > 0 || window.location.pathname !== path) return;
        replaceSearch(searchWithTab(window.location.search, "overview"));
      }, 0);
    };
  }, []);

  const setTab = useCallback((next: TaskTab) => {
    setTabState(next);
    replaceSearch(searchWithTab(window.location.search, next));
  }, []);

  return [tab, setTab];
}
