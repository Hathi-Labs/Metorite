"use client";

/**
 * The guide spotlight (WS-OB GD-2, D167, `assistant_guide.md` §3 G3, G10, §5.3).
 *
 * It rings ONE anchor on the page and shows one line next to it. It is not a
 * modal. Focus stays where it was, nothing goes inert, and the ring takes no
 * pointer, so the member can press the ringed element (G3).
 *
 * ## Why the ring and the popover go through a portal
 *
 * A glass ancestor has `backdrop-filter`, and that makes it the containing
 * block of every `position: fixed` child. Inside it, a fixed ring would measure
 * from the glass box and not from the window. The portal puts both on `body`.
 *
 * ## Why the popover is not an `AnchoredPanel`
 *
 * `AnchoredPanel` hangs a panel below or above its anchor from one edge, and it
 * never clamps to the window sides. At 390 px wide a popover that hangs from
 * the left edge of a right-hand anchor runs off the window. So this file
 * places the popover with {@link placePopover} and reuses the panel SURFACE
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

import { WATCHED_ATTRIBUTES, isShown } from "./guideAnchor";

export type GuideSpotlightProps = {
  /** An anchor id, e.g. "home.ask". Looks up the element whose guide attribute holds it. */
  anchor: string;
  /** The step line, shown in the popover and announced. */
  say: string;
  /** Shows "2 of 5" when given. `index` is 1-based. */
  step?: { index: number; total: number };
  /** Shows Next when given. */
  onNext?: () => void;
  /** Shows Back when given. */
  onBack?: () => void;
  /** The End button and Escape. */
  onEnd: () => void;
  /** The member pressed the ringed element. */
  onAnchorClick?: () => void;
  /** The anchor did not appear in time. Called once. */
  onMissing?: (anchor: string) => void;
  /** How long to wait for the anchor, in ms. Default 3000. */
  waitMs?: number;
  /**
   * Draw the popover (default true). Off draws the ring alone. On a phone the
   * step line is in the strip or the chat's step card (WS-OB GD-4, spec §4.3),
   * so a second copy would only cover it.
   */
  popover?: boolean;
};

export const DEFAULT_WAIT_MS = 3000;
export const POPOVER_WIDTH = 320;
const POPOVER_FALLBACK_HEIGHT = 120;
const RING_PAD = 4;
/** The ring line is 2 px outside its box, so a ring held this far from the window edge shows whole. */
export const RING_INSET = 4;
const GAP = 12;
const MARGIN = 8;
/** An anchor this tall (a share of the window) gets the popover beside it, not below it. */
const TALL_SHARE = 0.4;

type Rect = { left: number; top: number; right: number; bottom: number; width: number; height: number };

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
// Layer: both sit on the design system's `top` panel layer (`PANEL_LAYERS`),
// above the phone Menu drawer (z-70) and a Modal (z-50). The popover comes
// after the ring in the tree, so it paints over it. No z value is written here.

export function prefersReducedMotion(win: { matchMedia?: (q: string) => { matches: boolean } } | undefined): boolean {
  return win?.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
}

export function scrollBehaviorFor(reduced: boolean): ScrollBehavior {
  return reduced ? "auto" : "smooth";
}

/** Scroll the anchor into view with the least movement. */
export function scrollAnchorIntoView(el: Pick<HTMLElement, "scrollIntoView">, reduced: boolean): void {
  el.scrollIntoView({ block: "nearest", behavior: scrollBehaviorFor(reduced) });
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
 * The guide's own parts. The step card lives inside the assistant panel, which
 * is itself an anchor, so a press on Next there must not count as a press on
 * the ringed part. The strip is outside, and its mark is here for the same reason.
 */
export const OWN_PART = "[data-guide-card],[data-guide-strip],[data-guide-popover]";

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

// ------------------------------------------------------------------ views ---

/** "2 of 5". */
export function stepLabel(step: { index: number; total: number }): string {
  return `${step.index} of ${step.total}`;
}

/**
 * The popover's content. Exported so a test reads its markup. It holds NO live
 * region: `GuideHost` owns the one that announces each step at every width, so
 * a desktop does not announce the line twice.
 */
export function GuidePopoverBody(p: Pick<GuideSpotlightProps, "say" | "step" | "onNext" | "onBack" | "onEnd">) {
  return (
    <>
      <p className="text-sm text-foreground">{p.say}</p>
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
            Next
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

export function GuideSpotlight(props: GuideSpotlightProps) {
  const { anchor, say, step, onNext, onBack, onEnd, waitMs = DEFAULT_WAIT_MS, popover = true } = props;
  // Latest callbacks, so a parent re-render never restarts the wait.
  const latest = useRef(props);
  latest.current = props;
  const popRef = useRef<HTMLDivElement | null>(null);
  const [el, setEl] = useState<HTMLElement | null>(null);
  // Grows when the ringed anchor leaves the page, so the wait for it starts again.
  const [attempt, setAttempt] = useState(0);
  const [box, setBox] = useState<{ rect: Rect; pop: ReturnType<typeof placePopover> } | null>(null);

  useEffect(() => {
    setEl(null);
    setBox(null);
    const isReduced = prefersReducedMotion(window);
    return watchAnchor({
      anchor,
      waitMs,
      find: (id) => findAnchor(id),
      observe: observeDom,
      schedule: domScheduler,
      onFound: (found) => {
        scrollAnchorIntoView(found, isReduced);
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
        setBox(null);
        setAttempt((n) => n + 1);
      },
    });
  }, [el]);

  const place = useCallback(() => {
    if (!el) return;
    const r = el.getBoundingClientRect();
    const pr = popRef.current?.getBoundingClientRect();
    const size = {
      width: POPOVER_WIDTH,
      height: pr && pr.height > 0 ? pr.height : POPOVER_FALLBACK_HEIGHT,
    };
    const vp = { width: window.innerWidth, height: window.innerHeight };
    setBox({ rect: r, pop: placePopover(visibleRect(r, vp), size, vp) });
  }, [el]);

  useLayoutEffect(() => {
    if (!el) return;
    place();
    // Scroll is captured: the element that scrolls is rarely an ancestor of the portal.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    // A panel that slides in ends its move after the ring first measured it.
    window.addEventListener("transitionend", place, true);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("transitionend", place, true);
    };
  }, [el, place, say]);

  useEffect(() => bindEscape(document, () => latest.current.onEnd()), []);

  useEffect(() => {
    if (!el) return;
    return bindAnchorClick(el, () => latest.current.onAnchorClick?.());
  }, [el]);

  if (!el || !box) return null;
  const ring = clampRing(ringBox(box.rect), { width: window.innerWidth, height: window.innerHeight });

  return createPortal(
    <>
      <div
        aria-hidden="true"
        data-guide-ring=""
        style={{ left: ring.left, top: ring.top, width: ring.width, height: ring.height }}
        className={`pointer-events-none fixed ${PANEL_LAYERS.top} rounded-lg ring-2 ring-primary`}
      />
      {popover ? (
      <div
        ref={popRef}
        role="group"
        aria-label="Guide"
        data-guide-popover=""
        style={{ left: box.pop.left, top: box.pop.top, width: box.pop.width }}
        className={`fixed ${PANEL_LAYERS.top} p-3 ${PANEL_SURFACES.panel}`}
      >
        <GuidePopoverBody say={say} step={step} onNext={onNext} onBack={onBack} onEnd={onEnd} />
      </div>
      ) : null}
    </>,
    document.body,
  );
}

export default GuideSpotlight;
