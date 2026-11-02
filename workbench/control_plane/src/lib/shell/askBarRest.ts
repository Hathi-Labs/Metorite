/**
 * Where the Ask bar rests (owner, 2026-11-02: "anything where we don't have
 * to reserve the space at the bottom, so that we can use more of the desktop
 * workspace height?").
 *
 * The page keeps its whole height. The bar floats over it and takes the
 * largest form that hides no control:
 *
 *   full    the field, 36rem wide, at the foot of the page
 *   pill    "✦ Ask", 132 px, in the same place
 *   corner  a round button at the bottom right, lifted above any control
 *           under its corner
 *
 * The bar looks again 140 ms after a scroll stops, on a resize and when the
 * page changes. While the member uses the bar (focus, the pointer, the runs
 * sheet) it holds its form. The fold button sends it to the corner on one app
 * until ⌘K, `/` or a click brings it back, and it remembers that per app.
 *
 * The rule for "in the way": the bar covers the control's middle or more than
 * a tenth of it. A tenth, because a button's label sits at one end: the
 * measured case was a board lane's "+ Add", whose label the pill hid while it
 * covered a fifth of the button. Two kinds of target never count:
 *
 * - **Content that can still scroll clear.** The member scrolls it up from
 *   under the bar, as under any glass bar. Near the end of its scroll, when
 *   the scroll left is too short to lift it past the bar, it counts. The
 *   measured case: a lane's "+ Add" with 17 px of scroll left, under a bar
 *   it needed 60 px to clear.
 * - **A large target**, such as a task card. The part that shows is still a
 *   click on it. A field the member types in counts at any size.
 */

import { useEffect, useState, type RefObject } from "react";

export type AskRest = "full" | "pill" | "corner";

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** The full field's widest (`max-w-[36rem]`). */
export const ASK_FULL_MAX = 576;
/** The pill's width. */
export const ASK_PILL_WIDTH = 132;
/** The field's height: `h-11` and its 1 px border. */
export const ASK_BAR_HEIGHT = 46;
/** The gap under the bar and beside the corner (`bottom-5`). */
export const ASK_GAP = 20;
/** The space each side of the full field (`px-6`). */
export const ASK_SIDE = 24;
/** The corner button. */
export const ASK_CORNER_SIZE = 48;
/** The gap between a lifted corner button and the control under it. */
export const ASK_LIFT_GAP = 8;
/** The wait after the last scroll before the bar looks again. */
export const ASK_SETTLE_MS = 140;
/** A target taller than this is large: what shows of it is still a click. */
export const ASK_LARGE_TARGET = 64;

/** The corner button's box, `lift` pixels above the page's foot. */
export function cornerBox(area: Box, lift: number = ASK_GAP): Box {
  return {
    left: area.right - ASK_GAP - ASK_CORNER_SIZE,
    right: area.right - ASK_GAP,
    top: area.bottom - lift - ASK_CORNER_SIZE,
    bottom: area.bottom - lift,
  };
}

/** The boxes of the three forms in a page `area`, in viewport pixels. */
export function restBoxes(area: Box): Record<AskRest, Box> {
  const mid = (area.left + area.right) / 2;
  const bottom = area.bottom - ASK_GAP;
  const top = bottom - ASK_BAR_HEIGHT;
  const width = Math.max(0, Math.min(ASK_FULL_MAX, area.right - area.left - 2 * ASK_SIDE));
  const centred = (w: number): Box => ({ left: mid - w / 2, right: mid + w / 2, top, bottom });
  return {
    full: centred(width),
    pill: centred(ASK_PILL_WIDTH),
    corner: cornerBox(area),
  };
}

/** The share of a control the bar may cover before it is in the way. */
export const ASK_COVER_SHARE = 0.1;

/** The box covers the control's middle, or more than a tenth of it. */
export function inTheWay(control: Box, box: Box): boolean {
  const w = control.right - control.left;
  const h = control.bottom - control.top;
  if (w <= 0 || h <= 0) return false;
  const cx = (control.left + control.right) / 2;
  const cy = (control.top + control.bottom) / 2;
  if (cx > box.left && cx < box.right && cy > box.top && cy < box.bottom) return true;
  const iw = Math.max(0, Math.min(control.right, box.right) - Math.max(control.left, box.left));
  const ih = Math.max(0, Math.min(control.bottom, box.bottom) - Math.max(control.top, box.top));
  return (iw * ih) / (w * h) > ASK_COVER_SHARE;
}

/** The largest form that hides none of `controls`. The corner lifts instead. */
export function askRest(controls: readonly Box[], area: Box): AskRest {
  const boxes = restBoxes(area);
  if (!controls.some((c) => inTheWay(c, boxes.full))) return "full";
  if (!controls.some((c) => inTheWay(c, boxes.pill))) return "pill";
  return "corner";
}

/** The highest the corner button goes: half the page. */
export function maxLift(area: Box): number {
  return Math.max(ASK_GAP, (area.bottom - area.top) / 2);
}

/**
 * The next height of the corner button: where it is (`from`), or above the
 * highest control its box at that height is in the way of. The caller asks
 * again at the new height, until nothing moves it. A wide list row that the
 * corner only clips does not lift it.
 */
export function cornerLift(controls: readonly Box[], area: Box, from: number = ASK_GAP): number {
  const corner = cornerBox(area, from);
  let lift = from;
  for (const c of controls) {
    if (inTheWay(c, corner)) lift = Math.max(lift, area.bottom - c.top + ASK_LIFT_GAP);
  }
  return Math.min(lift, maxLift(area));
}

// ── The fold, per app ──────────────────────────────────────────────────────

const FOLD_KEY = "cc-ask-fold";

/** The app of a path: its first segment, so `/projects/42` folds with Projects. */
export function foldKey(pathname: string | null | undefined): string {
  const first = (pathname ?? "/").split("/").filter(Boolean)[0];
  return first ? `/${first}` : "/";
}

/** The apps where the member folded the bar. A blocked store reads as none. */
export function readFolds(): Set<string> {
  try {
    const raw = window.localStorage.getItem(FOLD_KEY);
    const list: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(list) ? list.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

/** Fold or unfold the bar on one app. A blocked store keeps the fold for this visit only. */
export function writeFold(key: string, folded: boolean): void {
  try {
    const folds = readFolds();
    if (folded) folds.add(key);
    else folds.delete(key);
    window.localStorage.setItem(FOLD_KEY, JSON.stringify([...folds]));
  } catch {
    /* the fold lasts until the page reloads */
  }
}

// ── What is under the bar (the browser) ────────────────────────────────────

/** A thing a click or the keyboard acts on. */
const TARGET =
  'a[href],button,input,select,textarea,summary,[contenteditable=""],[contenteditable="true"],[role="button"],[role="link"],[role="tab"],[role="checkbox"],[role="switch"],[role="menuitem"],[role="option"],[role="combobox"],[role="textbox"]';
/** A field the member types in. It counts at any size. */
const FIELD = 'input,select,textarea,[contenteditable=""],[contenteditable="true"],[role="textbox"],[role="combobox"]';

/** The sample step across a box, in pixels. A control is at least this big. */
const STEP_X = 12;
const STEP_Y = 10;

/**
 * A scroll of `left` pixels lifts a control whose foot is at `bottom` clear
 * of a bar whose top is at `barTop`, with the lift gap to spare.
 */
export function scrollClears(left: number, bottom: number, barTop: number): boolean {
  return left >= bottom - barTop + ASK_LIFT_GAP;
}

/** How far the nearest scroller of `el`, or the page, can still scroll down. */
function scrollLeft(el: Element): number {
  for (let node = el.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if ((overflowY === "auto" || overflowY === "scroll") && node.scrollHeight > node.clientHeight + 1) {
      return node.scrollHeight - node.clientHeight - node.scrollTop;
    }
  }
  const page = document.scrollingElement;
  return page ? page.scrollHeight - page.clientHeight - page.scrollTop : 0;
}

/**
 * The controls under `boxes` that the member can see and cannot scroll clear.
 * It asks the browser what is at points across each box, so a card a column
 * clipped, or a control under a dialog, does not count. `skip` is the bar.
 */
export function controlsUnder(boxes: readonly Box[], skip: readonly Element[]): Box[] {
  if (typeof document === "undefined" || typeof document.elementsFromPoint !== "function") return [];
  const found = new Set<Element>();
  const mine = (el: Element) => skip.some((s) => s.contains(el));
  for (const box of boxes) {
    for (let y = box.top + STEP_Y / 2; y < box.bottom; y += STEP_Y) {
      for (let x = box.left + STEP_X / 2; x < box.right; x += STEP_X) {
        const under = document.elementsFromPoint(x, y).find((el) => !mine(el));
        const target = under?.closest(TARGET);
        if (target && !mine(target)) found.add(target);
      }
    }
  }
  const barTop = Math.min(...boxes.map((b) => b.top));
  const out: Box[] = [];
  for (const el of found) {
    const r = el.getBoundingClientRect();
    if (r.height > ASK_LARGE_TARGET && !el.matches(FIELD)) continue;
    if (scrollClears(scrollLeft(el), r.bottom, barTop)) continue;
    out.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
  }
  return out;
}

/** A lift moves at most this many times in one look. */
const LIFT_TRIES = 6;

/** The form the page last gave the bar, per app, so a bar that mounts again starts there. */
const lastRest = new Map<string, { rest: AskRest; lift: number }>();

/**
 * The bar's resting form on the page that holds `wrapRef`. It looks when the
 * bar mounts, 140 ms after a scroll stops, on a resize and when the page
 * changes. `hold` freezes the form while the member uses the bar.
 */
export function useAskRest(
  wrapRef: RefObject<HTMLElement | null>,
  key: string,
  hold: boolean,
): { rest: AskRest; lift: number } {
  const [state, setState] = useState(() => lastRest.get(key) ?? { rest: "full" as AskRest, lift: ASK_GAP });
  useEffect(() => {
    const wrap = wrapRef.current;
    const page = wrap?.parentElement;
    if (!wrap || !page || hold) return;
    const look = () => {
      const r = page.getBoundingClientRect();
      const area: Box = { left: r.left, top: r.top, right: r.right, bottom: Math.min(r.bottom, window.innerHeight) };
      const rest = askRest(controlsUnder([restBoxes(area).full], [wrap]), area);
      let lift = ASK_GAP;
      if (rest === "corner") {
        for (let i = 0; i < LIFT_TRIES; i++) {
          const next = cornerLift(controlsUnder([cornerBox(area, lift)], [wrap]), area, lift);
          if (next === lift) break;
          lift = next;
        }
      }
      lastRest.set(key, { rest, lift });
      setState((s) => (s.rest === rest && s.lift === lift ? s : { rest, lift }));
    };
    let timer = 0;
    const settle = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(look, ASK_SETTLE_MS);
    };
    const first = window.requestAnimationFrame(look);
    // A scroll does not bubble, so the capture phase hears every scroller.
    document.addEventListener("scroll", settle, { capture: true, passive: true });
    window.addEventListener("resize", settle);
    // The bar's own changes (a run count) say nothing about the page.
    const changes = new MutationObserver((records) => {
      if (records.some((rec) => !wrap.contains(rec.target))) settle();
    });
    changes.observe(page, { childList: true, subtree: true });
    return () => {
      window.cancelAnimationFrame(first);
      window.clearTimeout(timer);
      document.removeEventListener("scroll", settle, { capture: true });
      window.removeEventListener("resize", settle);
      changes.disconnect();
    };
  }, [wrapRef, key, hold]);
  return state;
}
