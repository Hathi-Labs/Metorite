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
 *
 * This frame is not a `Modal`, because the AI pill must stay in reach (D172.4).
 * So it gives back by hand what the old drawer took from `Modal`: over the page
 * the page goes `inert`, and a close gives the focus to the last control the
 * member used, or to the landmark, with no scroll.
 */

import { type ReactNode, type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { focusLandmark, LANDMARK } from "@/components/ui/Modal";
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
  const aside = "flex h-full w-full flex-col bg-background outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring glass:bg-card";
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

/**
 * A popup, a dialog or a menu that holds this key. Each one owns its Escape.
 *
 * A bare `aria-expanded` is not a popup: an open fold of the Fields tab has one,
 * and Escape from there closes the card. Only a control that opens a popup counts.
 */
export const OVER_THE_CARD =
  '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [aria-haspopup][aria-expanded="true"], [role="combobox"][aria-expanded="true"]';

/** What can hold the focus: what a press on the page leaves it on. */
const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]';

/**
 * A click on the scrim closes the card only when the press began there too. A
 * drag that selects text in a note and ends on the scrim fires its click on the
 * host, and it must not close the card and lose the note.
 */
export function scrimCloses(pressedOnScrim: boolean, clickedOnScrim: boolean): boolean {
  return pressedOnScrim && clickedOnScrim;
}

/** The nodes `pageBehindCard` walks. A test passes plain objects. */
type TreeNode<T> = { parentElement: T | null; children: ArrayLike<T> };

/**
 * Everything of the page that is not the card: at each level from the card up
 * to the page root, the siblings of the card's line. New rows and banners inside
 * `<main>` are covered, because `<main>` itself is one of these.
 */
export function pageBehindCard<T extends TreeNode<T>>(page: T, card: T): T[] {
  const behind: T[] = [];
  for (let node: T | null = card; node && node !== page; node = node.parentElement) {
    const siblings: ArrayLike<T> = node.parentElement?.children ?? [];
    for (const other of Array.from(siblings)) {
      if (other !== node) behind.push(other);
    }
  }
  return behind;
}

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
 * The member's stop, side or full. Every app shares the one value. It reads the
 * storage before the first paint, so a stored `full` never flashes as `side`,
 * and not in the first render, because the server has no storage.
 */
export function usePanelMode(): [PanelMode, (next: PanelMode) => void] {
  const [mode, setMode] = useState<PanelMode>(DEFAULT_PANEL_MODE);
  useLayoutEffect(() => {
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
    // A rail that folds moves `<main>` and leaves the row the same width.
    watcher.observe(main);
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
  const hostRef = useRef<HTMLDivElement>(null);
  const pressedOnScrim = useRef(false);
  const statusRef = useRef<HTMLParagraphElement>(null);
  // Read in the first render: the effects below can blur it.
  const [opener] = useState<Element | null>(() =>
    typeof document === "undefined" ? null : document.activeElement,
  );

  // ⚠️ Keep this effect first. A cleanup runs in the order of the effects, and
  // the next one gives the focus back, which an `inert` page refuses.
  // Over the page, the page is out of the tab order and out of a screen reader.
  // The AI pill and the assistant are outside the page root, so they stay.
  useEffect(() => {
    const aside = asideRef.current;
    const page = aside?.closest(`[${CARD_PAGE_ATTR}]`);
    if (stop === "side" || !aside || !page) return;
    const behind = pageBehindCard<Element>(page, aside);
    for (const node of behind) node.setAttribute("inert", "");
    return () => {
      for (const node of behind) node.removeAttribute("inert");
    };
  }, [stop, asideRef]);

  // The focus goes back to the last control the member used on the page: the
  // opener first, then each row or card they used while the card stayed open.
  // With none it goes to the landmark, as a `Modal` does. It never scrolls, so a
  // long list stays where the member left it. It stays away when the member has
  // gone elsewhere: to the assistant, or to another page.
  useEffect(() => {
    const aside = asideRef.current;
    const page = aside?.closest(`[${CARD_PAGE_ATTR}]`) ?? null;
    let last = opener;
    const remember = (event: Event) => {
      const target = event.target;
      // The scrim is the card's own: a press on it must not forget the opener.
      if (!(target instanceof Element) || hostRef.current?.contains(target) || !page?.contains(target)) return;
      last = target.closest(FOCUSABLE);
    };
    window.addEventListener("focusin", remember);
    window.addEventListener("pointerdown", remember, true);
    return () => {
      window.removeEventListener("focusin", remember);
      window.removeEventListener("pointerdown", remember, true);
      if (!page || !document.contains(page)) return;
      const active = document.activeElement;
      if (active && active !== document.body && !page.contains(active)) return;
      if (last instanceof HTMLElement && last !== document.body && document.contains(last)) {
        last.focus({ preventScroll: true });
        return;
      }
      const landmark = document.querySelector<HTMLElement>(LANDMARK);
      if (landmark) focusLandmark(landmark);
    };
  }, [asideRef, opener]);

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

  // A docked card moves no focus, so a screen reader hears of it here. A status
  // line speaks when its words change, so an effect writes them after the mount.
  useEffect(() => {
    if (stop === "side" && statusRef.current) statusRef.current.textContent = label;
  }, [stop, label]);

  return (
    <div
      ref={hostRef}
      className={classes.host}
      role="presentation"
      onMouseDown={
        stop === "full"
          ? (event) => {
              pressedOnScrim.current = event.target === event.currentTarget;
            }
          : undefined
      }
      onClick={
        stop === "full"
          ? (event) => {
              if (scrimCloses(pressedOnScrim.current, event.target === event.currentTarget)) onClose();
              pressedOnScrim.current = false;
            }
          : undefined
      }
    >
      <div className={classes.card} role="presentation">
        <aside ref={asideRef} aria-label={label} tabIndex={-1} className={classes.aside}>
          <p ref={statusRef} role="status" className="sr-only" />
          {children}
        </aside>
      </div>
    </div>
  );
}
