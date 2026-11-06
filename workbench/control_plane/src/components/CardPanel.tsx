"use client";

/**
 * The frame of a record card (ORC-1, D181, `specs/one_record_card.md` §3.1).
 *
 * The owner asked for one card across the apps: "the way in which the card can
 * be opened as a sidebar as well as a centered card". One component draws the
 * three stops:
 *
 * - `side`: docked in the page row, after `<main>`, and the page stays usable.
 * - `full`: centered over the page column, with a scrim. The AI pill stays
 *   above it (`TASK_CARD_FRAME`, D172.4).
 * - `phone`: the whole screen, above the nav.
 *
 * ⚠️ **One mount.** The host mounts this once, in the page row, and the stop
 * only changes classes. So a change of stop keeps the tab, the scroll and a
 * half-typed note. At `side` the two wrappers are `display: contents`, so the
 * aside is the row's own column. At `full` the wrapper is `absolute`, and its
 * box is the page column, not the row, because the row has no position.
 *
 * Escape follows `panelEscape`, and it listens on the window and reads the DOM
 * with `contains`. A React handler on the aside would also hear a dialog that
 * a portal draws for a child of the card, and one Escape would close both.
 *
 * ⚠️ **A docked card must leave the page usable.** With the assistant docked
 * at 1440 wide, a docked card left the board 152 px and its toolbar broke. When
 * the row cannot hold the card and {@link PAGE_MIN_REM}, the card stands over
 * the page instead (`useCardStop`). The toggle hides, because it has nowhere
 * to go, and the member's choice stays stored for a wider page.
 */

import { type ReactNode, type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { TASK_CARD_FRAME } from "@/lib/layers";
import {
  DEFAULT_PANEL_MODE,
  PANEL_WIDTH_CLASS,
  type PanelEscape,
  type PanelMode,
  panelEscape,
  readPanelMode,
  writePanelMode,
} from "@/lib/panelMode";

/** Where a card stands: the member's choice, or the phone, which has no choice. */
export type CardStop = PanelMode | "phone";

/** The page root that holds the card. A key from inside it reaches the card. */
export const CARD_PAGE_ATTR = "data-card-page";

/** The width of a docked card: `TASK_PANEL_WIDTH`, `max-w-sm`. */
const SIDE_CARD_REM = 24;

/** The narrowest page beside a docked card: one list column and its tools. */
export const PAGE_MIN_REM = 20;

/**
 * Does the row hold a docked card and a usable page?
 *
 * *rowWidth* is the row, and *railWidth* is what else stands in it (the rail of
 * views). Neither one depends on the stop, so the answer cannot flip the stop
 * and flip back. An unmeasured row (0 before the first layout) says yes.
 */
export function sideCardFits(rowWidth: number, railWidth: number, rem: number): boolean {
  if (!(rowWidth > 0)) return true;
  return rowWidth - railWidth - SIDE_CARD_REM * rem >= PAGE_MIN_REM * rem;
}

/** The classes of the three parts at one stop. Pure, so a test reads them. */
export function cardFrameClasses(stop: CardStop): { host: string; card: string; aside: string } {
  const aside = "flex h-full w-full flex-col bg-background outline-none glass:bg-card";
  if (stop === "side") {
    return {
      host: "contents",
      card: "contents",
      aside: `${aside} shrink-0 ${PANEL_WIDTH_CLASS.side} border-l border-border`,
    };
  }
  if (stop === "full") {
    return {
      host: `${TASK_CARD_FRAME.desktop} flex items-stretch justify-center bg-background/70 p-4 sm:p-8`,
      card: `flex min-h-0 w-full ${PANEL_WIDTH_CLASS.full} overflow-hidden rounded-lg border border-border shadow-lg`,
      aside,
    };
  }
  return {
    host: `${TASK_CARD_FRAME.phone} flex bg-background pt-safe`,
    card: "flex min-h-0 w-full",
    aside,
  };
}

/** A popup, a dialog or a menu that holds this key. Each one owns its Escape. */
const OVER_THE_CARD =
  '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [aria-expanded="true"]';

type KeyEventLike = Parameters<typeof panelEscape>[0] & { defaultPrevented?: boolean };
type KeyTarget = NonNullable<Parameters<typeof panelEscape>[1]> & {
  closest?: (selector: string) => unknown;
};

/**
 * What Escape means for a card, or null for a key that is not the card's.
 *
 * `inReach` says the key came from inside the card or from the page that holds
 * it. So the chat composer of the assistant keeps its own Escape, and so does
 * the palette of the shell.
 */
export function cardEscape(
  event: KeyEventLike,
  target: KeyTarget | null,
  inReach: boolean,
  dialogOpen: boolean,
): PanelEscape {
  if (event.defaultPrevented || !inReach || dialogOpen) return null;
  if (target?.closest?.(OVER_THE_CARD)) return null;
  return panelEscape(event, target);
}

/**
 * The member's stop, side or full. It reads the storage after the first paint,
 * because the server has none, and every app shares the one value.
 */
export function usePanelMode(): [PanelMode, (next: PanelMode) => void] {
  const [mode, setMode] = useState<PanelMode>(DEFAULT_PANEL_MODE);
  useEffect(() => {
    // The server has no storage, so the first render cannot read it.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMode(readPanelMode());
  }, []);
  const choose = useCallback((next: PanelMode) => {
    setMode(next);
    writePanelMode(next);
  }, []);
  return [mode, choose];
}

/** Watch the row that holds the card. True while it holds a docked card too. */
function useSideCardFits(asideRef: RefObject<HTMLElement | null>): boolean {
  const [fits, setFits] = useState(true);
  useLayoutEffect(() => {
    const main = asideRef.current?.closest(`[${CARD_PAGE_ATTR}]`)?.querySelector("main");
    const row = main?.parentElement;
    if (!main || !row || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      // What else stands in the row: not `<main>`, and not the card's own host.
      const railWidth = Array.from(row.children).reduce(
        (sum, child) =>
          child === main || child.contains(asideRef.current) ? sum : sum + child.getBoundingClientRect().width,
        0,
      );
      setFits(sideCardFits(row.clientWidth, railWidth, rem));
    };
    measure();
    const watcher = new ResizeObserver(measure);
    watcher.observe(row);
    return () => watcher.disconnect();
  }, [asideRef]);
  return fits;
}

/**
 * The stop to draw, and the toggle for the shared header. A phone and a page
 * too narrow for a docked card have no toggle.
 */
export function useCardStop(phone: boolean): {
  stop: CardStop;
  expand: { expanded: boolean; onToggle: () => void } | undefined;
  asideRef: RefObject<HTMLElement | null>;
} {
  const [mode, setMode] = usePanelMode();
  const asideRef = useRef<HTMLElement>(null);
  const fits = useSideCardFits(asideRef);
  if (phone) return { stop: "phone", expand: undefined, asideRef };
  if (!fits) return { stop: "full", expand: undefined, asideRef };
  return {
    stop: mode,
    expand: { expanded: mode === "full", onToggle: () => setMode(mode === "full" ? "side" : "full") },
    asideRef,
  };
}

export default function CardPanel({
  stop,
  label,
  onClose,
  asideRef: givenRef,
  children,
}: {
  stop: CardStop;
  /** The name of the card for a screen reader: "Deal: Indian Army". */
  label: string;
  /** Every way out runs this: Escape, the scrim, and the host's close button. */
  onClose: () => void;
  /** The aside, for `useCardStop`, which measures the row around it. */
  asideRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const ownRef = useRef<HTMLElement>(null);
  const asideRef = givenRef ?? ownRef;
  const classes = cardFrameClasses(stop);

  // The opener takes the focus back on close. It is read on mount, not per
  // record, so a step to the next record still returns to where the trail
  // started. `contains`: a list that loaded again has dropped the old row.
  useEffect(() => {
    const opener = typeof document === "undefined" ? null : document.activeElement;
    return () => {
      if (opener instanceof HTMLElement && opener !== document.body && document.contains(opener)) {
        opener.focus();
      }
    };
  }, []);

  // A card over the page takes the focus, unless the focus is already inside
  // it, as it is on the toggle that expanded it. A docked card leaves the
  // focus on the board, so the keyboard stays where the member is working.
  // The focus stays too when it is outside the page: opening the assistant can
  // make the row too narrow, and its composer keeps what the member is typing.
  useEffect(() => {
    const aside = asideRef.current;
    if (stop === "side" || !aside) return;
    const active = document.activeElement;
    const page = aside.closest(`[${CARD_PAGE_ATTR}]`);
    if (aside.contains(active)) return;
    if (active && active !== document.body && page && !page.contains(active)) return;
    aside.focus({ preventScroll: true });
  }, [stop, asideRef]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const aside = asideRef.current;
      if (!aside) return;
      const target = event.target as (HTMLElement & KeyTarget) | null;
      const page = aside.closest(`[${CARD_PAGE_ATTR}]`);
      const inReach =
        target === document.body ||
        (target instanceof Node && (aside.contains(target) || (page?.contains(target) ?? false)));
      const dialogOpen = document.querySelector('[role="dialog"], [role="alertdialog"]') !== null;
      const action = cardEscape(event, target, inReach, dialogOpen);
      if (!action) return;
      if (action === "blur") target?.blur();
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, asideRef]);

  return (
    <div
      className={classes.host}
      role="presentation"
      onClick={stop === "full" ? onClose : undefined}
    >
      <div
        className={classes.card}
        role="presentation"
        onClick={stop === "full" ? (event) => event.stopPropagation() : undefined}
      >
        <aside ref={asideRef} aria-label={label} tabIndex={-1} className={classes.aside}>
          {children}
        </aside>
      </div>
    </div>
  );
}
