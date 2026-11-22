"use client";

/**
 * The guide spotlight (WS-OB GD-2, D167, `assistant_guide.md` §3 G3, G10, §5.3).
 *
 * It rings ONE anchor on the page and draws ONE tour card beside it. It is not
 * a modal. Focus stays where it was, nothing goes inert, and the ring takes no
 * pointer, so the member can press the ringed element (G3).
 *
 * ## The tour card (owner request, 2026-11-03)
 *
 * The card holds the line, "3 of 5", Back, Next or Done, End tour, and on the
 * last stop the app chips. It floats in the guide's top layer, above the dim.
 * It never sits in the side chat: there it mixed in with the chat's own
 * buttons. On a desktop it sits beside the ring. On a phone it docks to the
 * window edge farther from the ring ({@link placeCard}).
 *
 * ## Why the ring and the card go through a portal
 *
 * A glass ancestor has `backdrop-filter`, and that makes it the containing
 * block of every `position: fixed` child. Inside it, a fixed ring would measure
 * from the glass box and not from the window. `GuideHost` puts the dim, the
 * ring and the card in ONE portal on `body`, in that order, so the card paints
 * over the dim.
 *
 * ## Why the card is not an `AnchoredPanel`
 *
 * `AnchoredPanel` hangs a panel below or above its anchor from one edge, and it
 * never clamps to the window sides. At 390 px wide a card that hangs from
 * the left edge of a right-hand anchor runs off the window. So this file
 * places the card with {@link placePopover} and reuses the panel SURFACE
 * (`PANEL_SURFACES.panel`), so the glass look stays one look.
 *
 * ## Tests
 *
 * `vitest.config.ts` runs in a node environment and the slice adds no DOM test
 * library. So each rule is a pure function or an injected controller below,
 * and `GuideSpotlight.test.ts` drives them with fakes.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { PANEL_LAYERS, PANEL_SURFACES } from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { revealWithin, type ScrollBox, type Span } from "@/lib/scrollWithin";

import { ANCHOR_MISSING_NOTE, nextLabel, type TourChip } from "./guide";
import { useAssistant } from "./assistant";
import { WATCHED_ATTRIBUTES, isShown } from "./guideAnchor";

export type TourCardProps = {
  /** The step line. */
  say: string;
  /** Shows "2 of 5" when given. `index` is 1-based. */
  step?: { index: number; total: number };
  /** Shows Next when given. */
  onNext?: () => void;
  /** Shows Back when given. */
  onBack?: () => void;
  /** The End button and Escape. */
  onEnd: () => void;
  /** The last stop: the primary button says "Done", and pressing it ends the tour. */
  last?: boolean;
  /** The app chips of the tour's last stop (GD-5a). None on any other stop. */
  chips?: readonly TourChip[];
  onChip?: (chip: TourChip) => void;
  /** A phone: the card docks to the window edge farther from the ring. */
  phone?: boolean;
};

export type GuideSpotlightProps = Pick<TourCardProps, "say" | "onEnd"> & {
  /** An anchor id, e.g. "home.ask". Looks up the element whose guide attribute holds it. */
  anchor: string;
  /** The member pressed the ringed element. */
  onAnchorClick?: () => void;
  /** The anchor did not appear in time. Called once. */
  onMissing?: (anchor: string) => void;
  /**
   * The anchor's box, in window coordinates, or null when none is on screen.
   * The one measure: the ring, the dim's hole and the tour card all read it,
   * so they never disagree (review round, 2026-11-04).
   */
  onRect?: (rect: Rect | null) => void;
  /** How long to wait for the anchor, in ms. Default 3000. */
  waitMs?: number;
};


export const DEFAULT_WAIT_MS = 3000;
export const CARD_WIDTH = 320;
const CARD_FALLBACK_HEIGHT = 120;
/** The phone card keeps this gutter at each window side and at the edge it docks to. */
export const PHONE_GUTTER = 16;
/** A card narrower than this is no card: the phone dock rule takes over. */
export const CARD_MIN_WIDTH = 240;
const RING_PAD = 4;
/** The ring line is 2 px outside its box, so a ring held this far from the window edge shows whole. */
export const RING_INSET = 4;
const GAP = 12;
const MARGIN = 8;
/** An anchor this tall (a share of the window) gets the popover beside it, not below it. */
const TALL_SHARE = 0.4;
/** An anchor this tall (a share of the window) leaves the phone card no free edge. */
export const FILLS_SHARE = 0.6;

export type Rect = { left: number; top: number; right: number; bottom: number; width: number; height: number };

// ---------------------------------------------------------------- lookup ---

/** Escape an id for a quoted attribute value. Uses `CSS.escape` when the host has it. */
export function escapeId(id: string): string {
  const css = (globalThis as { CSS?: { escape?: (s: string) => string } }).CSS;
  if (css?.escape) return css.escape(id);
  return id.replace(/[^a-zA-Z0-9_-]/g, (c) => "\\" + c);
}

/**
 * The attribute an anchor carries. A constant, so fence F1
 * (`tests/unit/test_guide_anchors.py`) never reads this file as an anchor.
 */
const GUIDE_ATTR = "data-guide";

/** The selector for an anchor id. The id can never end the attribute or add a selector. */
export function anchorSelector(id: string): string {
  return `[${GUIDE_ATTR}="${escapeId(id)}"]`;
}

/** The first element of the list that `visible` accepts, or null. Pure, so a test drives it. */
export function pickVisible<T>(els: Iterable<T>, visible: (el: T) => boolean): T | null {
  for (const el of els) if (visible(el)) return el;
  return null;
}

/**
 * The first VISIBLE element that carries the anchor, or null. `home.ask` draws
 * twice (the top bar and the assistant panel foot), and on a phone one copy can
 * be hidden, so the first match in the tree is not always the one to ring.
 */
export function findAnchor(id: string, root: ParentNode = document): HTMLElement | null {
  return pickVisible(root.querySelectorAll<HTMLElement>(anchorSelector(id)), isShown);
}

// ------------------------------------------------------------ wait, miss ---

export type Scheduler = { set: (fn: () => void, ms: number) => unknown; clear: (handle: unknown) => void };

/**
 * Wait up to `waitMs` for an anchor. It finds the anchor now, or when the DOM
 * changes, or it calls `onMissing` once. It never ends in silence. Returns a
 * cancel function that stops both the watch and the timer.
 */
export function watchAnchor(o: {
  anchor: string;
  waitMs: number;
  find: (id: string) => HTMLElement | null;
  observe: (onChange: () => void) => () => void;
  schedule: Scheduler;
  onFound: (el: HTMLElement) => void;
  onMissing: (anchor: string) => void;
}): () => void {
  let done = false;
  let stopObserve: (() => void) | null = null;
  let timer: unknown = null;
  const finish = () => {
    done = true;
    stopObserve?.();
    if (timer !== null) o.schedule.clear(timer);
  };
  const check = () => {
    if (done) return;
    const el = o.find(o.anchor);
    if (!el) return;
    finish();
    o.onFound(el);
  };
  check();
  if (done) return finish;
  stopObserve = o.observe(check);
  timer = o.schedule.set(() => {
    if (done) return;
    finish();
    o.onMissing(o.anchor);
  }, o.waitMs);
  return finish;
}

// ---------------------------------------------------------------- motion ---
//
// The ring and the popover have NO transition. Both follow their anchor on
// every scroll frame, and a transition on left, top, width or height makes
// them trail it (`DESIGN_SYSTEM.md` section 11, `motion.test.ts` G3). A class
// is never built at run time either: Tailwind would not generate it. Rule 8
// of `GuideSpotlight.test.ts` holds both.
//
// Layer: the dim, the ring and the card sit on the design system's `guide` panel
// layer (`PANEL_LAYERS`), above the phone Menu drawer (z-70), the phone's bottom
// bar (z-96, which the tour dims and rings) and a Modal (z-50). They come in that order in ONE portal, so the card paints over the
// ring and the ring over the dim. No z value is written here.

export function prefersReducedMotion(win: { matchMedia?: (q: string) => { matches: boolean } } | undefined): boolean {
  return win?.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
}

export function scrollBehaviorFor(reduced: boolean): ScrollBehavior {
  return reduced ? "auto" : "smooth";
}

/**
 * The nearest box above the anchor that scrolls, inside the app shell. It never
 * returns the shell or a box above it, so no ancestor of the shell moves
 * (`src/lib/scrollWithin.ts`, fence `scrollWithin.test.ts`). Null when none
 * scrolls, for example an anchor in the sidebar.
 */
export function scrollerOf(el: Element): HTMLElement | null {
  for (let p = el.parentElement; p && !p.hasAttribute("data-app-shell"); p = p.parentElement) {
    const overflow = getComputedStyle(p).overflowY;
    if ((overflow === "auto" || overflow === "scroll") && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
}

/**
 * Show the anchor with the least movement of ONE scroller, the rule of
 * `block: "nearest"`. `Element.scrollIntoView` would also move the boxes above
 * it, the app shell too (owner report, 2026-10-10).
 */
export function scrollAnchorIntoView(
  el: { getBoundingClientRect(): Span },
  reduced: boolean,
  scroller: ScrollBox | null | undefined,
): void {
  revealWithin(scroller, el, scrollBehaviorFor(reduced));
}

// ------------------------------------------------------------------- keys ---

/**
 * Escape ends the tour in the same frame (G10). The handler calls `onEnd`
 * synchronously, with no timer and no exit animation. Returns the remover.
 */
export function bindEscape(
  doc: Pick<Document, "addEventListener" | "removeEventListener">,
  onEnd: () => void,
): () => void {
  const handler = (e: Event) => {
    if ((e as KeyboardEvent).key === "Escape") onEnd();
  };
  doc.addEventListener("keydown", handler, true);
  return () => doc.removeEventListener("keydown", handler, true);
}

/**
 * The guide's own part. The tour card floats over the page, and an anchor can
 * lie under it (the card sits beside the ring, but the window can be small), so
 * a press on the card must not count as a press on the ringed part.
 */
export const OWN_PART = "[data-guide-card]";

/** Tell the tour the member pressed the anchor. The page keeps its own click. */
export function bindAnchorClick(
  el: Pick<HTMLElement, "addEventListener" | "removeEventListener">,
  onClick: () => void,
): () => void {
  const handler = (e?: Event) => {
    const t = e?.target as { closest?: (s: string) => unknown } | null | undefined;
    if (t?.closest?.(OWN_PART)) return;
    onClick();
  };
  el.addEventListener("click", handler);
  return () => el.removeEventListener("click", handler);
}

// -------------------------------------------------------------- placement ---

type Box = { left: number; top: number; width: number; height: number };
type Viewport = { width: number; height: number };

/** The ring box: the anchor's box plus a small pad. */
export function ringBox(rect: Rect, pad = RING_PAD): Box {
  return { left: rect.left - pad, top: rect.top - pad, width: rect.width + pad * 2, height: rect.height + pad * 2 };
}

/**
 * The ring box held inside the window, `inset` from every edge, so the whole
 * line shows. An anchor taller than the window (My Day) or a panel that fills
 * it (the phone sheet) would otherwise ring off screen.
 */
export function clampRing(box: Box, viewport: Viewport, inset = RING_INSET): Box {
  const left = Math.max(inset, box.left);
  const top = Math.max(inset, box.top);
  const right = Math.min(viewport.width - inset, box.left + box.width);
  const bottom = Math.min(viewport.height - inset, box.top + box.height);
  return { left, top, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
}

/** The part of the anchor that is inside the window. The popover places against this. */
export function visibleRect(rect: Rect, viewport: Viewport): Rect {
  const left = Math.min(Math.max(rect.left, 0), viewport.width);
  const top = Math.min(Math.max(rect.top, 0), viewport.height);
  const right = Math.max(left, Math.min(rect.right, viewport.width));
  const bottom = Math.max(top, Math.min(rect.bottom, viewport.height));
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}

/**
 * Where the popover sits. Beside a tall anchor, or when neither below nor
 * above has room, it goes to the right of an anchor on the left half and to
 * the left of one on the right half, so it never covers the anchor when the
 * window has room. Otherwise it prefers below, flips above when below has no
 * room, and clamps to the window on every side. The width shrinks on a narrow
 * window, so it fits at 390 px.
 */
export function placePopover(
  rect: Rect,
  size: { width: number; height: number },
  viewport: Viewport,
): { left: number; top: number; width: number; side: "below" | "above" | "right" | "left" } {
  const width = Math.min(size.width, Math.max(0, viewport.width - MARGIN * 2));
  const spaceBelow = viewport.height - rect.bottom - GAP - MARGIN;
  const spaceAbove = rect.top - GAP - MARGIN;
  const vertical = size.height <= spaceBelow || size.height <= spaceAbove;
  if (rect.height >= viewport.height * TALL_SHARE || !vertical) {
    const spaceRight = viewport.width - rect.right - GAP - MARGIN;
    const spaceLeft = rect.left - GAP - MARGIN;
    const rightFits = spaceRight >= width;
    const leftFits = spaceLeft >= width;
    if (rightFits || leftFits) {
      const onLeftHalf = rect.left + rect.width / 2 < viewport.width / 2;
      const right = onLeftHalf ? rightFits : !leftFits;
      const rawLeft = right ? rect.right + GAP : rect.left - GAP - width;
      const rawTop = rect.top + rect.height / 2 - size.height / 2;
      return {
        left: Math.max(MARGIN, Math.min(rawLeft, viewport.width - MARGIN - width)),
        top: Math.max(MARGIN, Math.min(rawTop, viewport.height - MARGIN - size.height)),
        width,
        side: right ? "right" : "left",
      };
    }
  }
  const below = size.height <= spaceBelow || spaceBelow >= spaceAbove;
  const rawTop = below ? rect.bottom + GAP : rect.top - GAP - size.height;
  const top = Math.max(MARGIN, Math.min(rawTop, viewport.height - MARGIN - size.height));
  const rawLeft = rect.left + rect.width / 2 - width / 2;
  const left = Math.max(MARGIN, Math.min(rawLeft, viewport.width - MARGIN - width));
  return { left, top, width, side: below ? "below" : "above" };
}

/** Which window edge the phone card docks to. */
export type DockEdge = "top" | "bottom";

/**
 * The window edge FARTHER from the anchor: the top edge when the anchor's
 * centre is in the lower half, else the bottom edge. No anchor docks to the top,
 * clear of the message field. An anchor that fills most of the window (the phone
 * sheet) leaves no room on either side, so the card docks to the top and keeps
 * the message field at the foot clear. Pure.
 */
export function dockEdge(rect: Rect | null, viewport: Viewport): DockEdge {
  if (!rect) return "top";
  if (rect.height >= viewport.height * FILLS_SHARE) return "top";
  return rect.top + rect.height / 2 > viewport.height / 2 ? "top" : "bottom";
}

export type CardPlace = {
  left: number;
  top: number;
  width: number;
  side: "below" | "above" | "right" | "left" | "top" | "bottom";
  /**
   * The card is docked to a window edge. The view adds the safe-area inset to
   * that edge (`viewport-fit=cover`), so a notch never covers the card.
   */
  docked?: "top" | "bottom";
};

/** The phone dock: full width less the gutter, on the edge farther from the anchor. */
export function dockPlace(rect: Rect | null, size: { width: number; height: number }, viewport: Viewport): CardPlace {
  const width = Math.max(0, viewport.width - PHONE_GUTTER * 2);
  const edge = dockEdge(rect, viewport);
  const lowest = Math.max(PHONE_GUTTER, viewport.height - PHONE_GUTTER - size.height);
  return { left: PHONE_GUTTER, top: edge === "top" ? PHONE_GUTTER : lowest, width, side: edge, docked: edge };
}

/**
 * Where the tour card sits. `rect` is the part of the anchor in the window, or
 * null when the step has no anchor on screen (a step with none, or a missing one).
 *
 * - Phone: {@link dockPlace}.
 * - Desktop: beside the anchor by {@link placePopover}.
 * - No anchor on a desktop: centred at the foot of the window.
 * - On a desktop, the card never covers the open assistant panel (`panel`).
 *   If the first place meets the panel, the card moves to the left of it and
 *   lines up with the anchor: its bottom edge on the bottom edge of an anchor
 *   in the lower half, else its top edge on the anchor's top edge. The card
 *   shrinks to the free width. Under {@link CARD_MIN_WIDTH} it takes the phone
 *   dock instead.
 *
 * The card always sits inside the window. Pure.
 */
export function placeCard(
  rect: Rect | null,
  size: { width: number; height: number },
  viewport: Viewport,
  phone: boolean,
  panel: Rect | null = null,
): CardPlace {
  if (phone) return dockPlace(rect, size, viewport);
  const first = placeCardFirst(rect, size, viewport);
  if (!panel || panel.width <= 0 || panel.height <= 0) return first;
  const meets =
    first.left < panel.right &&
    panel.left < first.left + first.width &&
    first.top < panel.bottom &&
    panel.top < first.top + size.height;
  if (!meets) return first;
  const free = panel.left - GAP - PHONE_GUTTER;
  if (free < CARD_MIN_WIDTH) return dockPlace(rect, size, viewport);
  const width = Math.min(first.width, free);
  const lowHalf = rect ? rect.top + rect.height / 2 > viewport.height / 2 : true;
  const rawTop = !rect ? viewport.height - PHONE_GUTTER - size.height : lowHalf ? rect.bottom - size.height : rect.top;
  const maxTop = Math.max(PHONE_GUTTER, viewport.height - PHONE_GUTTER - size.height);
  return {
    left: Math.max(PHONE_GUTTER, panel.left - GAP - width),
    top: Math.min(Math.max(PHONE_GUTTER, rawTop), maxTop),
    width,
    side: "left",
  };
}

function placeCardFirst(rect: Rect | null, size: { width: number; height: number }, viewport: Viewport): CardPlace {
  if (!rect) {
    const width = Math.min(size.width, Math.max(0, viewport.width - MARGIN * 2));
    return {
      left: Math.max(MARGIN, (viewport.width - width) / 2),
      top: Math.max(MARGIN, viewport.height - MARGIN - size.height),
      width,
      side: "bottom",
    };
  }
  return placePopover(rect, size, viewport);
}

// ------------------------------------------------------------------ views ---

/** "2 of 5". */
export function stepLabel(step: { index: number; total: number }): string {
  return `${step.index} of ${step.total}`;
}

/**
 * The tour card's content. Exported so a test reads its markup. It holds NO
 * live region: `GuideHost` owns the one that announces each step, so a screen
 * reader hears the line once.
 */
export function GuideCardBody(p: TourCardProps & { missing?: boolean }) {
  const chips = p.chips ?? [];
  return (
    <>
      <p className="text-sm text-foreground">{p.say}</p>
      {p.missing ? <p className="mt-1 text-xs text-muted-foreground">{ANCHOR_MISSING_NOTE}</p> : null}
      {chips.length > 0 ? (
        <div data-guide-chips="" role="group" aria-label="Open an app" className="mt-2 flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <Button key={c.href} variant="secondary" size="sm" icon={c.icon} onClick={() => p.onChip?.(c)}>
              {c.label}
            </Button>
          ))}
        </div>
      ) : null}
      <div className="mt-3 flex items-center gap-2">
        <span className="mr-auto text-xs text-muted-foreground">{p.step ? stepLabel(p.step) : ""}</span>
        {p.onBack ? (
          <Button variant="ghost" size="sm" onClick={p.onBack}>
            Back
          </Button>
        ) : null}
        <Button variant="ghost" size="sm" onClick={p.onEnd}>
          End tour
        </Button>
        {p.onNext ? (
          <Button variant="primary" size="sm" onClick={p.onNext}>
            {nextLabel(p.last)}
          </Button>
        ) : null}
      </div>
    </>
  );
}

/**
 * Watch an anchor that is on screen. When it leaves the tree or hides (a tap
 * on the drawer scrim removes the Menu drawer), `onGone` runs once and the
 * watch stops. Pure, so a test drives it. Returns the cancel function.
 */
export function watchDetached(o: {
  isGone: () => boolean;
  observe: (onChange: () => void) => () => void;
  onGone: () => void;
}): () => void {
  let done = false;
  let stop: (() => void) | null = null;
  const check = () => {
    if (done || !o.isGone()) return;
    done = true;
    stop?.();
    o.onGone();
  };
  stop = o.observe(check);
  if (done) stop();
  return () => {
    done = true;
    stop?.();
  };
}

function sameRect(a: Rect | null, b: Rect | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.left === b.left && a.top === b.top && a.width === b.width && a.height === b.height;
}

/** The open side panel's box on a desktop, kept current. Null when none is open or on a phone. */
function usePanelRect(phone: boolean): Rect | null {
  // The assistant store is the seam: an open, a close or a form change re-measures.
  const st = useAssistant();
  const [panel, setPanel] = useState<Rect | null>(null);
  useLayoutEffect(() => {
    if (phone) return setPanel((v) => (v === null ? v : null));
    const measure = () => {
      const el = document.querySelector<HTMLElement>('[data-assistant="dock"]');
      const r = el && isShown(el) ? el.getBoundingClientRect() : null;
      setPanel((v) => (sameRect(v, r) ? v : r));
    };
    measure();
    window.addEventListener("resize", measure);
    // The dock opens and resizes under a transition: the end of it measures again.
    window.addEventListener("transitionend", measure, true);
    const stop = observeDom(measure);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("transitionend", measure, true);
      stop();
    };
  }, [phone, st.open, st.mode]);
  return panel;
}

/**
 * The tour card. It floats in the top layer, with the accent edge that the
 * ring has, on the glass panel surface. The edge is an outline and not a
 * `ring-2` box shadow: the glass surface sets its own shadow and hid the ring.
 *
 * `GuideHost` mounts ONE of it for the whole tour, with no per-step key, so a
 * member who tabs to Next and presses Enter keeps the focus (the guide never
 * calls `.focus()`, spec section 5.3). It measures its own height, so a card
 * docked to the bottom edge sits on it.
 *
 * - `rect` is the anchor's box, or null when the step has no anchor on screen.
 * - `pending`: the step has an anchor that is not measured yet. The card keeps
 *   its last place, or draws nothing on the first step, so it never jumps.
 * - `missing`: the anchor did not appear. The card adds the reason.
 */
export function TourCard(p: TourCardProps & { rect: Rect | null; missing?: boolean; pending?: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const last = useRef<CardPlace | null>(null);
  const [vp, setVp] = useState<Viewport | null>(null);
  const [height, setHeight] = useState(CARD_FALLBACK_HEIGHT);
  const panel = usePanelRect(!!p.phone);

  useLayoutEffect(() => {
    const read = () =>
      setVp((v) => {
        const next = { width: window.innerWidth, height: window.innerHeight };
        return v && v.width === next.width && v.height === next.height ? v : next;
      });
    read();
    window.addEventListener("resize", read);
    return () => window.removeEventListener("resize", read);
  }, []);

  // After each draw: the height can change with the line, the chips and the width.
  useLayoutEffect(() => {
    const h = ref.current?.getBoundingClientRect().height ?? 0;
    if (h > 0 && Math.abs(h - height) > 0.5) setHeight(h);
  });

  if (!vp) return null;
  let at: CardPlace;
  if (p.pending && last.current) at = last.current;
  else if (p.pending) return null;
  else {
    at = placeCard(p.rect ? visibleRect(p.rect, vp) : null, { width: CARD_WIDTH, height }, vp, !!p.phone, panel);
    last.current = at;
  }
  // A docked card keeps clear of the notch and the home bar.
  const edge =
    at.docked === "bottom"
      ? { bottom: `calc(${PHONE_GUTTER}px + env(safe-area-inset-bottom, 0px))` }
      : at.docked === "top"
        ? { top: `calc(${PHONE_GUTTER}px + env(safe-area-inset-top, 0px))` }
        : { top: at.top };
  return (
    <div
      ref={ref}
      role="group"
      aria-label="Guide"
      data-guide-card=""
      data-guide-side={at.side}
      style={{ left: at.left, width: at.width, ...edge }}
      className={`fixed ${PANEL_LAYERS.guide} p-3 outline outline-2 outline-primary ${PANEL_SURFACES.panel}`}
    >
      <GuideCardBody {...p} />
    </div>
  );
}

// ------------------------------------------------------------------- dim ---
//
// WS-OB GD-2 (owner request, 2026-11-03): the member could not find the tour
// among the other cards on screen. While a step is up, ONE dim layer covers the
// window and leaves ONE clear hole: the ringed part. The tour card sits ABOVE
// the dim, so it needs no hole. A missing anchor leaves no hole, and the dim
// covers the window under the card.
// The fill is the one scrim token of `DESIGN_SYSTEM.md` section 4a, the one
// `ui/Modal` uses. The layer takes no pointer, so a press and a Tab reach the
// page as before (spec section 3 G3, section 5.3). It has no transition, so it
// leaves in the same frame as the ring (G10).

/** A clear hole in the dim layer, with its corner radius. */
export type DimHole = { left: number; top: number; width: number; height: number; r: number };

/** The corner radius of the ring (`rounded-lg`). */
const ANCHOR_RADIUS = 8;

/**
 * The holes of the dim layer: the anchor's, which has the ring's box (padded,
 * clamped to the window), or none when the anchor is missing. Pure.
 */
export function dimHoles(anchor: Rect | null, viewport: Viewport): DimHole[] {
  if (!anchor) return [];
  const b = clampRing(ringBox(anchor), viewport);
  return b.width > 0 && b.height > 0 ? [{ ...b, r: ANCHOR_RADIUS }] : [];
}

const MASK_ID = "guide-dim-mask";

export type GuideDimProps = {
  /**
   * The anchor's box from the spotlight's ONE measure, or null. The dim never
   * looks the anchor up itself, so its hole and the ring never disagree.
   */
  rect: Rect | null;
};

/**
 * The dim layer. `GuideHost` mounts it only for a step that has an anchor
 * (found, or missing), and never for a step with no part to point at: there
 * the member must use the page. With no rect it covers the whole window. It
 * returns the layer and not a portal: `GuideHost` puts it FIRST in the one
 * guide portal, so the ring and the card come after it and paint over it.
 */
export function GuideDim({ rect }: GuideDimProps) {
  // The window size, read again on a resize. The hole follows `rect`.
  const [vp, setVp] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }));
  useLayoutEffect(() => {
    const read = () =>
      setVp((v) => {
        const n = { width: window.innerWidth, height: window.innerHeight };
        return v.width === n.width && v.height === n.height ? v : n;
      });
    read();
    window.addEventListener("resize", read);
    return () => window.removeEventListener("resize", read);
  }, []);
  const holes = dimHoles(rect, vp);
  return (
    <svg
      aria-hidden="true"
      data-guide-dim=""
      className={`pointer-events-none fixed inset-0 ${PANEL_LAYERS.guide} h-full w-full`}
    >
      <defs>
        <mask id={MASK_ID} maskUnits="userSpaceOnUse" x="0" y="0" width="100%" height="100%">
          <rect x="0" y="0" width="100%" height="100%" fill="white" />
          {holes.map((h, i) => (
            <rect key={i} data-guide-hole="" x={h.left} y={h.top} width={h.width} height={h.height} rx={h.r} fill="black" />
          ))}
        </mask>
      </defs>
      <rect x="0" y="0" width="100%" height="100%" mask={`url(#${MASK_ID})`} className="fill-background/80" />
    </svg>
  );
}

const domScheduler: Scheduler = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

function observeDom(onChange: () => void): () => void {
  const mo = new MutationObserver(onChange);
  mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: WATCHED_ATTRIBUTES });
  // A panel that slides in ends its move with no DOM change (the phone sheet rises
  // under a transition), so the end of a move and a short poll check again.
  document.addEventListener("transitionend", onChange, true);
  document.addEventListener("animationend", onChange, true);
  const poll = window.setInterval(onChange, 200);
  return () => {
    mo.disconnect();
    document.removeEventListener("transitionend", onChange, true);
    document.removeEventListener("animationend", onChange, true);
    window.clearInterval(poll);
  };
}

/**
 * The ring of a step with an anchor. It finds the anchor, measures it, and
 * reports the box up (`onRect`). It returns the ring without a portal:
 * `GuideHost` holds the one portal, with the dim before it and the one tour
 * card after it.
 */
export function GuideSpotlight(props: GuideSpotlightProps) {
  const { anchor, waitMs = DEFAULT_WAIT_MS } = props;
  // Latest callbacks, so a parent re-render never restarts the wait.
  const latest = useRef(props);
  latest.current = props;
  const [el, setEl] = useState<HTMLElement | null>(null);
  // Grows when the ringed anchor leaves the page, so the wait for it starts again.
  const [attempt, setAttempt] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);

  useEffect(() => {
    setEl(null);
    setRect(null);
    const isReduced = prefersReducedMotion(window);
    return watchAnchor({
      anchor,
      waitMs,
      find: (id) => findAnchor(id),
      observe: observeDom,
      schedule: domScheduler,
      onFound: (found) => {
        scrollAnchorIntoView(found, isReduced, scrollerOf(found));
        setEl(found);
      },
      onMissing: (id) => latest.current.onMissing?.(id),
    });
  }, [anchor, waitMs, attempt]);

  // The anchor leaves the tree or hides: the ring goes at once, and the wait
  // for the anchor starts again. If it does not return, `onMissing` runs.
  useEffect(() => {
    if (!el) return;
    return watchDetached({
      isGone: () => !el.isConnected || !isShown(el),
      observe: observeDom,
      onGone: () => {
        setEl(null);
        setRect(null);
        setAttempt((n) => n + 1);
      },
    });
  }, [el]);

  const place = useCallback(() => {
    if (!el) return;
    const r = el.getBoundingClientRect();
    setRect((v) => (v && sameRect(v, r) ? v : r));
  }, [el]);

  useLayoutEffect(() => {
    if (!el) return;
    place();
    // Scroll is captured: the element that scrolls is rarely an ancestor of the portal.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    // A panel that slides in ends its move after the ring first measured it.
    window.addEventListener("transitionend", place, true);
    // An anchor can move with no scroll, resize or transition (a list that
    // grows above it): the DOM watch and its short poll measure again.
    const stop = observeDom(place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("transitionend", place, true);
      stop();
    };
  }, [el, place, props.say]);

  // The ONE measure goes up to the host, which feeds the dim and the card.
  useEffect(() => {
    latest.current.onRect?.(rect);
  }, [rect]);
  useEffect(() => () => latest.current.onRect?.(null), []);

  useEffect(() => bindEscape(document, () => latest.current.onEnd()), []);

  useEffect(() => {
    if (!el) return;
    return bindAnchorClick(el, () => latest.current.onAnchorClick?.());
  }, [el]);

  if (!el || !rect) return null;
  const ring = clampRing(ringBox(rect), { width: window.innerWidth, height: window.innerHeight });

  return (
    <>
      <div
        aria-hidden="true"
        data-guide-ring=""
        style={{ left: ring.left, top: ring.top, width: ring.width, height: ring.height }}
        className={`pointer-events-none fixed ${PANEL_LAYERS.guide} rounded-lg ring-2 ring-primary`}
      />
    </>
  );
}

export default GuideSpotlight;
