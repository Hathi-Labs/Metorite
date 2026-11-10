"use client";

/**
 * Page state of a Studio page, read from and written to the URL (STU-D11b).
 *
 * The URL is the only source of truth. This hook keeps no copy of the state in React
 * state, so a reload, a shared link and Back all give the state that the URL holds. Every
 * read goes through `decodePageState`, so `boundPageState` binds it. A hostile URL gives
 * a safe state, never an error.
 *
 * - A tab change and a record pick use `router.push`. Back then returns to the earlier tab
 *   or closes the side view.
 * - A filter edit uses `router.replace`, 300 ms after the last edit. Typing makes no history entry.
 * - A write keeps each query key that this hook does not own (`utm_*`, `task`).
 *
 * The component that calls this hook sits inside a `Suspense` boundary, because
 * `useSearchParams` needs one for a static build.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { boundPageState, decodePageState, encodePageState, type PageState, type PageTabs } from "./pageState";

/** The wait after the last filter edit, in milliseconds. */
export const FILTER_DEBOUNCE_MS = 300;

const OWNED = (key: string): boolean => key === "sel" || key === "tab" || key.startsWith("f.");

/** The query string that holds `state` and every foreign key of `current`. Pure. */
export function mergePageQuery(current: string, state: PageState, page?: PageTabs): string {
  const next = new URLSearchParams();
  for (const [k, v] of new URLSearchParams(current).entries()) if (!OWNED(k)) next.append(k, v);
  for (const [k, v] of new URLSearchParams(encodePageState(boundPageState(state, page))).entries()) next.append(k, v);
  return next.toString();
}

/**
 * Whether the unmount of the page writes the pending filter. It does when an edit waits and the
 * member is still on this page. A member who left for another route must not be pulled back.
 */
export function shouldFlushOnUnmount(hasPending: boolean, here: string, pagePath: string): boolean {
  return hasPending && here === pagePath;
}

export interface PageStateApi {
  state: PageState;
  /** Show a tab. Adds a history entry. */
  setTab: (tab: string) => void;
  /** Open a record in the side view, or close it with `null`. Adds a history entry. */
  setSelected: (id: string | null) => void;
  /** Set one filter. An empty value clears it. Replaces the entry, 300 ms after the last edit. */
  setFilter: (name: string, value: string) => void;
}

export function usePageState(page?: PageTabs): PageStateApi {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const search = params.toString();
  const tabsKey = (page?.tabs ?? []).map((t) => t.id).join(",");
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `tabsKey` stands for the tab ids.
  const pageTabs = useMemo<PageTabs | undefined>(() => (page ? { tabs: page.tabs } : undefined), [tabsKey, !page]);
  const state = useMemo(() => decodePageState(search, pageTabs), [search, pageTabs]);

  // The latest values, so a timer or a stable callback never writes from a stale render.
  const live = useRef({ search, pathname, pageTabs, router });
  live.current = { search, pathname, pageTabs, router };
  const pending = useRef<Record<string, string>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const write = useCallback((change: (s: PageState) => PageState, mode: "push" | "replace") => {
    const { search: cur, pathname: path, pageTabs: tabs, router: r } = live.current;
    const base = decodePageState(cur, tabs);
    const next = change({ ...base, filters: { ...base.filters, ...pending.current } });
    pending.current = {};
    const qs = mergePageQuery(cur, next, tabs);
    r[mode](qs ? `${path}?${qs}` : path, { scroll: false });
  }, []);

  const flush = useCallback(() => {
    timer.current = null;
    if (Object.keys(pending.current).length) write((s) => s, "replace");
  }, [write]);

  const setTab = useCallback((tab: string) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    write((s) => ({ ...s, tab }), "push");
  }, [write]);

  const setSelected = useCallback((id: string | null) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    write((s) => ({ ...s, selected: id }), "push");
  }, [write]);

  const setFilter = useCallback((name: string, value: string) => {
    pending.current = { ...pending.current, [name]: value };
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(flush, FILTER_DEBOUNCE_MS);
  }, [flush]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
    // An edit typed in the last 300 ms is written now, so it is not lost.
    if (shouldFlushOnUnmount(Object.keys(pending.current).length > 0, window.location.pathname, live.current.pathname)) {
      timer.current = null;
      write((s) => s, "replace");
    }
  }, [write]);

  return useMemo(() => ({ state, setTab, setSelected, setFilter }), [state, setTab, setSelected, setFilter]);
}
