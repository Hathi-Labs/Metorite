"use client";

/**
 * The panel on My Day — the page's half (PK-1, task 2469, D204,
 * `my_day_feed.md` §11.5). Behind `NEXT_PUBLIC_MY_DAY_PEEK`, default OFF.
 *
 * ONE seam on My Day. Every card keeps its plain `<a>` and its own markup,
 * and My Day decides in one place what a click on a row does:
 *
 * - **A click.** `onClickCapture` on the page column sees the click before
 *   the link does. A plain left click on a link that the panel takes
 *   (`peekOfClick`) opens the panel, and the link does nothing. Any other
 *   click is the link's, so Ctrl-click opens Projects in a new tab.
 * - **A link in code.** A menu item goes through `useMyDayGo`
 *   (`myDayGo.ts`), and this hook provides its `peek`.
 * - **The URL.** The panel lives in `?peek=task:<id>` on `/`. The page
 *   pushes one history entry for each open, so Back closes the panel and a
 *   reload opens it again. Next keeps `useSearchParams` in step with
 *   `history.pushState`, and the panel reads only that.
 * - **The frame** is the record card (`components/CardPanel.tsx`). It owns
 *   Escape, the focus return and `inert`. The panel reports its stop, so My
 *   Day draws one column only while the card stands beside the page.
 * - **The open row** wears `PEEK_OPEN_ATTR`, and its link `aria-current`.
 *   The page column is watched, so a re-read of the feed keeps the mark.
 * - **The rows.** A panel that wrote something asks My Day to read its rows
 *   again when it closes (`onSettled`), so the row shows the change.
 *
 * With the flag off, the hook gives nothing: no handler, no reader and no
 * provider. So a row opens its app, as before.
 */

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import {
  type MouseEvent,
  type ReactNode,
  type RefObject,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type { CardStop } from "@/components/CardPanel";

import type { PeekOpener } from "./myDayGo";
import {
  PEEK_DEPTH_KEY,
  PEEK_OPEN_ATTR,
  PEEK_PARAM,
  PEEK_ROW,
  type PeekTarget,
  clickedLinkOf,
  closeMove,
  isDocked,
  landingClears,
  peekDepthOf,
  peekFromParam,
  peekHref,
  peekOfClick,
  peekParamOf,
  peekTargetOf,
} from "./myDayPeek";

/** The panel and the task body, loaded on the first open only. */
const MyDayPeekPanel = dynamic(() => import("./MyDayPeekPanel"), { ssr: false });

/**
 * The `peek` value of the address bar, through Next's own search params,
 * and the whole query, so the page hears any change to the address.
 */
function PeekParamReader({ onParam }: { onParam: (value: string | null, search: string) => void }) {
  const params = useSearchParams();
  const value = params?.get(PEEK_PARAM) ?? null;
  const search = params?.toString() ?? "";
  useEffect(() => onParam(value, search), [value, search, onParam]);
  return null;
}

/** The `peek` value in the address bar right now. */
function peekInAddress(): PeekTarget | null {
  return peekFromParam(new URLSearchParams(window.location.search).get(PEEK_PARAM));
}

export interface MyDayPeek {
  /** The page column (a `div` with the column mark): the rows to take a click from and to mark. */
  columnRef: RefObject<HTMLDivElement | null>;
  /** The page column's click handler, or undefined with the flag off. */
  onClickCapture: ((event: MouseEvent<HTMLElement>) => void) | undefined;
  /** What `useMyDayGo` asks, or null with the flag off. */
  peek: PeekOpener | null;
  /** The reader of the address bar, and the panel when it is open. */
  reader: ReactNode;
  panel: ReactNode;
  /**
   * The card stands as a column beside the page (`isDocked`). The page's
   * grid is set by the WINDOW width (`xl:`), so then My Day draws its cards
   * in one column (`wide` in `MyDayPage.tsx`). Over the page, the grid stays.
   */
  docked: boolean;
}

export function useMyDayPeek(
  enabled: boolean,
  options: { me?: string; onSettled: () => void },
): MyDayPeek {
  const [param, setParam] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stop, setStop] = useState<CardStop | null>(null);
  const target = enabled ? peekFromParam(param) : null;
  const targetKey = target ? peekParamOf(target) : null;
  const columnRef = useRef<HTMLDivElement | null>(null);
  const changed = useRef(false);
  const settle = useRef(options.onSettled);
  useEffect(() => {
    settle.current = options.onSettled;
  });

  /**
   * How many entries the page pushed for the panel, up to the one shown.
   *
   * ⚠️ `history.state` alone cannot hold it. A part that mirrors its own key
   * in the address calls `replaceState(null, …)`: the task card's tab
   * (`useTaskTab`, `?ttab=`) does, and that drops our key. So this ref keeps
   * the count, a `popstate` reads the count of the entry it lands on, and the
   * effect below writes the count back after another part dropped it.
   */
  const depth = useRef(0);
  // A close that walks back. The entry it lands on loses its panel too when
  // the page did not push it (`landingClears`): ✕ means "close".
  const closing = useRef(false);
  useEffect(() => {
    const onPop = () => {
      depth.current = peekDepthOf(window.history.state);
      const wasClosing = closing.current;
      closing.current = false;
      if (!landingClears(wasClosing, depth.current, peekInAddress() !== null)) return;
      // After Next's own traversal of this entry, never inside it.
      window.setTimeout(() => {
        window.history.replaceState({}, "", peekHref(window.location, null));
        setParam(null);
      }, 0);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  useEffect(() => {
    if (!targetKey) {
      depth.current = 0;
      return;
    }
    const state = window.history.state as Record<string, unknown> | null;
    // A reload keeps `history.state`, so a panel that opens from the address
    // takes the count its entry carries.
    if (depth.current === 0) depth.current = peekDepthOf(state);
    // A state that carries `__NA` passes Next's patch as its own, so this
    // moves no URL and starts no navigation.
    if (depth.current > 0 && state?.__NA && peekDepthOf(state) !== depth.current) {
      window.history.replaceState({ ...state, [PEEK_DEPTH_KEY]: depth.current }, "");
    }
  }, [targetKey, search]);

  const open = useCallback((next: PeekTarget) => {
    // The same task again is no new entry: Back must not need two presses.
    const shown = peekInAddress();
    if (shown && shown.kind === next.kind && shown.id === next.id) return;
    const below = shown ? depth.current : 0;
    depth.current = below + 1;
    window.history.pushState({ [PEEK_DEPTH_KEY]: depth.current }, "", peekHref(window.location, next));
    setParam(peekParamOf(next));
  }, []);

  const close = useCallback(() => {
    const move = closeMove(depth.current);
    if ("go" in move) {
      // Back over the page's own entries. Next's reader then sees no panel.
      closing.current = true;
      window.history.go(move.go);
      return;
    }
    depth.current = 0;
    window.history.replaceState({}, "", peekHref(window.location, null));
    setParam(null);
  }, []);

  // A phone bar tab goes on to its own page at once. A traversal here would
  // race that push, so the panel closes in place (`MyDayPeekPanel`'s
  // `onLeave`).
  const leave = useCallback(() => {
    depth.current = 0;
    window.history.replaceState({}, "", peekHref(window.location, null));
    setParam(null);
  }, []);

  // The rows again, once, when a panel that wrote something closes.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (targetKey) {
      wasOpen.current = true;
      return;
    }
    if (wasOpen.current && changed.current) settle.current();
    wasOpen.current = false;
    changed.current = false;
    setStop(null);
  }, [targetKey]);

  // The open row: the house selected tint on its row, and `aria-current` on
  // its link. A feed re-read draws new nodes, so the column is watched.
  useEffect(() => {
    const column = columnRef.current;
    if (!column || !targetKey) return;
    const mark = () => {
      const rows = new Set<Element>();
      const links = new Set<Element>();
      for (const link of column.querySelectorAll("a[href]")) {
        const at = peekTargetOf(link.getAttribute("href"));
        if (!at || peekParamOf(at) !== targetKey) continue;
        links.add(link);
        rows.add(link.closest(PEEK_ROW) ?? link);
      }
      for (const el of column.querySelectorAll(`[${PEEK_OPEN_ATTR}]`)) {
        if (!rows.has(el)) el.removeAttribute(PEEK_OPEN_ATTR);
      }
      for (const el of column.querySelectorAll('a[aria-current="true"]')) {
        if (!links.has(el)) el.removeAttribute("aria-current");
      }
      for (const el of rows) if (!el.hasAttribute(PEEK_OPEN_ATTR)) el.setAttribute(PEEK_OPEN_ATTR, "");
      for (const el of links) if (el.getAttribute("aria-current") !== "true") el.setAttribute("aria-current", "true");
    };
    mark();
    // Only the tree is watched, never an attribute, so a mark wakes nothing.
    const watch = new MutationObserver(mark);
    watch.observe(column, { childList: true, subtree: true });
    return () => {
      watch.disconnect();
      for (const el of column.querySelectorAll(`[${PEEK_OPEN_ATTR}]`)) el.removeAttribute(PEEK_OPEN_ATTR);
      for (const el of column.querySelectorAll('a[aria-current="true"]')) el.removeAttribute("aria-current");
    };
  }, [targetKey]);

  const onClickCapture = useCallback(
    (event: MouseEvent<HTMLElement>) => {
      const next = peekOfClick(event, clickedLinkOf(event));
      if (!next) return;
      // The link's own handler sees `defaultPrevented` and stays still.
      event.preventDefault();
      open(next);
    },
    [open],
  );

  const peek = useCallback<PeekOpener>(
    (href) => {
      const next = peekTargetOf(href);
      if (!next) return false;
      open(next);
      return true;
    },
    [open],
  );

  const onParam = useCallback((value: string | null, query: string) => {
    setParam(value);
    setSearch(query);
  }, []);

  const markChanged = useCallback(() => {
    changed.current = true;
  }, []);

  if (!enabled) {
    return { columnRef, onClickCapture: undefined, peek: null, reader: null, panel: null, docked: false };
  }
  return {
    columnRef,
    onClickCapture,
    peek,
    // `useSearchParams` needs a Suspense boundary at build (`OrgLinkGate.tsx`).
    reader: (
      <Suspense fallback={null}>
        <PeekParamReader onParam={onParam} />
      </Suspense>
    ),
    panel: target ? (
      <MyDayPeekPanel
        target={target}
        onClose={close}
        onLeave={leave}
        onOpen={open}
        onChanged={markChanged}
        onStop={setStop}
        me={options.me}
      />
    ) : null,
    docked: isDocked(!!target, stop),
  };
}
