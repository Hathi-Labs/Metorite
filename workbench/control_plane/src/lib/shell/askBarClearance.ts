/**
 * Room under the last line (owner, 2026-11-07, task 2242): "it would make
 * sense to have extra scroll down so that all the elements appear above the
 * chat box across all of the apps".
 *
 * The Ask bar floats over the page column (`askBarRest.ts`) and reserves no
 * space. So the last card of a long pane ends under the field and cannot
 * scroll clear. Padding on `<main>` does not help: the pane that scrolls is
 * nested inside the app, and `<main>` seldom overflows. The room must go to
 * the INNERMOST vertical scroller that the bar overlaps.
 *
 * This file is the one seam. `planClearance` is pure and decides which
 * scrollers get room and how much. `useAskBarClearance` reads the page, calls
 * it, and writes `data-askbar-clear` with two custom properties on each chosen
 * element. The rule in `globals.css` turns them into padding:
 *
 *   padding-bottom: calc(var(--askbar-pb) + var(--askbar-extra))
 *
 * `--askbar-pb` is the padding the element had before the mark, so the
 * element keeps its own space (and `.pb-askbar`, which already leaves room,
 * gets an extra of zero).
 *
 * Fences: `askBarClearance.test.ts` (the rules) and
 * `e2e/askbar-clearance.spec.ts` (the layout, which only a browser measures).
 */

import { useEffect, type RefObject } from "react";

import { ASK_BAR_HEIGHT, ASK_FULL_MAX, ASK_GAP, ASK_SIDE, type Box } from "./askBarRest";

/** The gap between the last line and the bar's top edge. */
export const CLEARANCE_GAP = 16;
/** A scroller shorter than this is a widget (a list box, a code block), not a pane. */
export const CLEARANCE_MIN_HEIGHT = 80;
/** The wait between two scans of a busy page. */
export const CLEARANCE_SETTLE_MS = 120;

export const MARK = "data-askbar-clear";
const EXTRA = "--askbar-extra";
const BASE = "--askbar-pb";

/** What the browser says about one element that might scroll. */
export interface ScrollerFacts {
  id: number;
  /** Viewport pixels. */
  rect: Box;
  /** Computed `overflow-y`. */
  overflowY: string;
  /** Computed `display`. */
  display: string;
  /** Computed `flex-direction`. */
  flexDirection: string;
  clientHeight: number;
  /** The element scrolls sideways and cannot scroll down: a table or tab strip. */
  horizontalOnly: boolean;
  /** Its own padding-bottom, without any extra this seam added. */
  paddingBottom: number;
  /**
   * The lowest edge that an ancestor clips it to. A box that runs past it does
   * not scroll: it grows, and the ancestor is the scroller.
   */
  clipBottom: number;
  /** The ids of the candidates that contain it. */
  ancestors: readonly number[];
}

/** The bar's footprint in a page column: the full field, whatever form it rests in. */
export function barFootprint(area: Box): Box {
  const width = Math.max(0, Math.min(ASK_FULL_MAX, area.right - area.left - 2 * ASK_SIDE));
  const mid = (area.left + area.right) / 2;
  const bottom = area.bottom - ASK_GAP;
  return { left: mid - width / 2, right: mid + width / 2, top: bottom - ASK_BAR_HEIGHT, bottom };
}

/** Can the element be a pane? A vertical scroller laid out top to bottom. */
function isPane(f: ScrollerFacts): boolean {
  if (f.overflowY !== "auto" && f.overflowY !== "scroll") return false;
  if (f.clientHeight < CLEARANCE_MIN_HEIGHT || f.horizontalOnly) return false;
  if (f.rect.bottom > f.clipBottom + 1) return false;
  if (f.display === "flex" || f.display === "inline-flex") {
    return f.flexDirection === "column" || f.flexDirection === "column-reverse";
  }
  return ["block", "flow-root", "grid", "inline-block", "list-item"].includes(f.display);
}

/**
 * The extra padding, in pixels, for each scroller that needs room. A scroller
 * counts when it is a pane, overlaps the bar sideways and ends below the bar's
 * top. Only the deepest counts: a pane that holds another counted pane gets
 * nothing. The extra is the room the bar needs, less the padding the pane has.
 */
export function planClearance(
  bar: Box | null,
  scrollers: readonly ScrollerFacts[],
  gap: number = CLEARANCE_GAP,
): Map<number, number> {
  const plan = new Map<number, number>();
  if (!bar) return plan;
  const counted = scrollers.filter(
    (s) => isPane(s) && s.rect.right > bar.left && s.rect.left < bar.right && s.rect.bottom > bar.top,
  );
  const holders = new Set(counted.flatMap((s) => s.ancestors));
  for (const s of counted) {
    if (holders.has(s.id)) continue;
    const needed = s.rect.bottom - bar.top + gap;
    const extra = Math.max(0, Math.ceil(needed - s.paddingBottom));
    if (extra > 0) plan.set(s.id, extra);
  }
  return plan;
}

// ── The browser side ───────────────────────────────────────────────────────

function unmark(el: HTMLElement) {
  el.removeAttribute(MARK);
  el.style.removeProperty(EXTRA);
  el.style.removeProperty(BASE);
}

/** Remove every mark under `column`. */
export function clearMarks(column: ParentNode) {
  column.querySelectorAll<HTMLElement>(`[${MARK}]`).forEach(unmark);
}

function px(v: string): number {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

/** The lowest edge that an ancestor below `column` (or the column) clips `el` to. */
function clipBottomOf(el: HTMLElement, column: HTMLElement, columnBottom: number): number {
  let edge = columnBottom;
  for (let node = el.parentElement; node && node !== column; node = node.parentElement) {
    if (getComputedStyle(node).overflowY === "visible") continue;
    edge = Math.min(edge, node.getBoundingClientRect().bottom);
  }
  return edge;
}

/**
 * One scan: read the column, plan, write the marks.
 *
 * The scan starts from a clean slate. A mark that an earlier scan wrote moves
 * the boxes it measures: padding on `<main>` once squeezed the board under it
 * to end above the bar, and the scan then took `<main>` for the pane. So it
 * lifts every mark, reads, and writes the new marks in one task, so no frame
 * paints between. A lifted mark can clamp a scroll position, so it keeps and
 * restores each `scrollTop`.
 */
export function scanClearance(column: HTMLElement, area: Box) {
  const kept = [...column.querySelectorAll<HTMLElement>(`[${MARK}]`)].map((el) => [el, el.scrollTop] as const);
  kept.forEach(([el]) => unmark(el));
  const found = new Set<HTMLElement>(column.querySelectorAll<HTMLElement>('[class*="overflow"]'));
  column.querySelectorAll<HTMLElement>("main").forEach((m) => found.add(m));
  const els = [...found].filter((el) => !el.closest('[role="dialog"],[aria-modal="true"]'));
  const ids = new Map<HTMLElement, number>(els.map((el, i) => [el, i]));
  const facts: ScrollerFacts[] = [];
  for (const el of els) {
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden") continue;
    facts.push({
      id: ids.get(el)!,
      clipBottom: clipBottomOf(el, column, area.bottom),
      rect: el.getBoundingClientRect(),
      overflowY: cs.overflowY,
      display: cs.display,
      flexDirection: cs.flexDirection,
      clientHeight: el.clientHeight,
      horizontalOnly: el.scrollWidth > el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1,
      paddingBottom: px(cs.paddingBottom),
      ancestors: els.filter((o) => o !== el && o.contains(el)).map((o) => ids.get(o)!),
    });
  }
  const plan = planClearance(barFootprint(area), facts);
  for (const f of facts) {
    const extra = plan.get(f.id);
    if (!extra) continue;
    const el = els[f.id];
    el.style.setProperty(BASE, `${f.paddingBottom}px`);
    el.setAttribute(MARK, "");
    el.style.setProperty(EXTRA, `${extra}px`);
  }
  kept.forEach(([el, top]) => {
    if (el.scrollTop !== top) el.scrollTop = top;
  });
}

/**
 * Keep the marks right for the page column that `areaRef` covers. The scan
 * runs at most once per `CLEARANCE_SETTLE_MS`, after the column or its
 * children change, and on a window resize. It does not run on a phone, and it
 * clears every mark when `enabled` turns false or the bar leaves.
 */
export function useAskBarClearance(areaRef: RefObject<HTMLElement | null>, enabled: boolean) {
  useEffect(() => {
    const area = areaRef.current;
    const column = area?.parentElement;
    if (!enabled || !area || !column) return;
    const desktop = window.matchMedia("(min-width: 640px)");
    let timer: number | null = null;
    let frame = 0;
    const run = () => {
      timer = null;
      if (!desktop.matches) {
        clearMarks(column);
        return;
      }
      const r = area.getBoundingClientRect();
      scanClearance(column, { left: r.left, top: r.top, right: r.right, bottom: r.bottom });
    };
    const schedule = () => {
      if (timer !== null) return;
      timer = window.setTimeout(() => {
        frame = window.requestAnimationFrame(run);
      }, CLEARANCE_SETTLE_MS);
    };
    frame = window.requestAnimationFrame(run);
    const resize = new ResizeObserver(schedule);
    resize.observe(area);
    const mutate = new MutationObserver(schedule);
    mutate.observe(column, { childList: true, subtree: true });
    window.addEventListener("resize", schedule);
    desktop.addEventListener("change", schedule);
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      window.cancelAnimationFrame(frame);
      resize.disconnect();
      mutate.disconnect();
      window.removeEventListener("resize", schedule);
      desktop.removeEventListener("change", schedule);
      clearMarks(column);
    };
  }, [areaRef, enabled]);
}
