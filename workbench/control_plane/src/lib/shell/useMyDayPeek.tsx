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
 *   (`peekTargetOf`) opens the panel, and the link does nothing. Any other
 *   click is the link's, so Ctrl-click opens Projects in a new tab.
 * - **A link in code.** A menu item goes through `useMyDayGo`
 *   (`myDayGo.ts`), and this hook provides its `peek`.
 * - **The URL.** The panel lives in `?peek=task:<id>` on `/`. The page
 *   pushes one history entry for each open, so Back closes the panel and a
 *   reload opens it again. Next keeps `useSearchParams` in step with
 *   `history.pushState`, and the panel reads only that.
 * - **Escape.** From the page or from nowhere, Escape closes the panel
 *   (`cardEscape`, the record card's rule). From inside the panel,
 *   `TaskPanel` owns it.
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

import { cardEscape } from "@/components/CardPanel";

import type { PeekOpener } from "./myDayGo";
import {
  PEEK_DEPTH_KEY,
  PEEK_FRAME_ATTR,
  PEEK_PARAM,
  type PeekTarget,
  closeMove,
  peekDepthOf,
  peekFromParam,
  peekHref,
  peekParamOf,
  peekTakesClick,
  peekTargetOf,
} from "./myDayPeek";

/** The panel and the task body, loaded on the first open only. */
const MyDayPeekPanel = dynamic(() => import("./MyDayPeekPanel"), { ssr: false });

/** The `peek` value of the address bar, through Next's own search params. */
function PeekParamReader({ onParam }: { onParam: (value: string | null) => void }) {
  const params = useSearchParams();
  const value = params?.get(PEEK_PARAM) ?? null;
  useEffect(() => onParam(value), [value, onParam]);
  return null;
}

/** The `peek` value in the address bar right now. */
function peekInAddress(): PeekTarget | null {
  return peekFromParam(new URLSearchParams(window.location.search).get(PEEK_PARAM));
}

export interface MyDayPeek {
  /** The page root. A key from inside it reaches the panel's Escape. */
  pageRef: RefObject<HTMLDivElement | null>;
  /** The page column's click handler, or undefined with the flag off. */
  onClickCapture: ((event: MouseEvent<HTMLElement>) => void) | undefined;
  /** What `useMyDayGo` asks, or null with the flag off. */
  peek: PeekOpener | null;
  /** The reader of the address bar, and the panel when it is open. */
  reader: ReactNode;
  panel: ReactNode;
}

export function useMyDayPeek(
  enabled: boolean,
  options: { me?: string; onSettled: () => void },
): MyDayPeek {
  const [param, setParam] = useState<string | null>(null);
  const target = enabled ? peekFromParam(param) : null;
  const targetKey = target ? peekParamOf(target) : null;
  const pageRef = useRef<HTMLDivElement | null>(null);
  const changed = useRef(false);
  const settle = useRef(options.onSettled);
  useEffect(() => {
    settle.current = options.onSettled;
  });

  const open = useCallback((next: PeekTarget) => {
    // The same task again is no new entry: Back must not need two presses.
    const shown = peekInAddress();
    if (shown && shown.kind === next.kind && shown.id === next.id) return;
    const depth = peekDepthOf(window.history.state);
    window.history.pushState({ [PEEK_DEPTH_KEY]: depth + 1 }, "", peekHref(window.location, next));
    setParam(peekParamOf(next));
  }, []);

  const close = useCallback(() => {
    const move = closeMove(peekDepthOf(window.history.state));
    if ("go" in move) {
      // Back over the page's own entries. Next's reader then sees no panel.
      window.history.go(move.go);
      return;
    }
    window.history.replaceState({}, "", peekHref(window.location, null));
    setParam(null);
  }, []);

  // A phone bar tab goes on to its own page at once. A traversal here would
  // race that push, so the panel closes in place (`MyDayPeekPanel`'s
  // `onLeave`).
  const leave = useCallback(() => {
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
  }, [targetKey]);

  // Escape from the page, or from nowhere, closes the panel.
  useEffect(() => {
    if (!targetKey) return;
    const onKey = (event: KeyboardEvent) => {
      const page = pageRef.current;
      const target = event.target as (HTMLElement & Node) | null;
      const frame = page?.querySelector(`[${PEEK_FRAME_ATTR}]`) ?? null;
      // Inside the panel, `TaskPanel` owns Escape (blur a field, then close).
      if (frame && target instanceof Node && frame.contains(target)) return;
      const inReach = target === document.body || (target instanceof Node && !!page?.contains(target));
      const dialogOpen = document.querySelector('[role="dialog"], [role="alertdialog"]') !== null;
      if (cardEscape(event, target, inReach, dialogOpen) === "close") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [targetKey, close]);

  const onClickCapture = useCallback(
    (event: MouseEvent<HTMLElement>) => {
      const link = (event.target as Element | null)?.closest?.("a[href]");
      if (!(link instanceof HTMLAnchorElement) || !event.currentTarget.contains(link)) return;
      if (!peekTakesClick(event, { target: link.target, download: link.hasAttribute("download") })) return;
      const next = peekTargetOf(link.getAttribute("href"));
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

  const markChanged = useCallback(() => {
    changed.current = true;
  }, []);

  if (!enabled) {
    return { pageRef, onClickCapture: undefined, peek: null, reader: null, panel: null };
  }
  return {
    pageRef,
    onClickCapture,
    peek,
    // `useSearchParams` needs a Suspense boundary at build (`OrgLinkGate.tsx`).
    reader: (
      <Suspense fallback={null}>
        <PeekParamReader onParam={setParam} />
      </Suspense>
    ),
    panel: target ? (
      <MyDayPeekPanel target={target} onClose={close} onLeave={leave} onOpen={open} onChanged={markChanged} me={options.me} />
    ) : null,
  };
}
