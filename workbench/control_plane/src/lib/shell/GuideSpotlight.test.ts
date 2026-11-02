/**
 * The guide spotlight, one test per access rule (`assistant_guide.md` §5.3).
 *
 * `vitest.config.ts` is `environment: "node"` and has no DOM library. As in
 * `InfoTip.test.ts`, each rule is a pure function or an injected controller,
 * driven here with fakes, and the markup is read with `renderToStaticMarkup`.
 * The rules that need a live browser (focus, Tab) are fenced on the source and
 * walked by the GD-4 e2e.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  GuidePopoverBody,
  anchorSelector,
  bindAnchorClick,
  bindEscape,
  motionClasses,
  placePopover,
  prefersReducedMotion,
  ringBox,
  scrollAnchorIntoView,
  watchAnchor,
} from "./GuideSpotlight";

const SRC = readFileSync(join(__dirname, "GuideSpotlight.tsx"), "utf-8");
const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

/** A scheduler that runs only when the test says so. */
function fakeClock() {
  const timers = new Map<number, () => void>();
  let n = 0;
  return {
    schedule: {
      set: (fn: () => void) => {
        timers.set(++n, fn);
        return n;
      },
      clear: (h: unknown) => void timers.delete(h as number),
    },
    fire: () => [...timers.values()].forEach((f) => f()),
    pending: () => timers.size,
  };
}

function watch(found: { el: object | null }) {
  const clock = fakeClock();
  const calls = { found: [] as object[], missing: [] as string[] };
  let change: () => void = () => {};
  let observing = false;
  watchAnchor({
    anchor: "home.ask",
    waitMs: 3000,
    find: () => found.el as HTMLElement | null,
    observe: (cb) => {
      change = cb;
      observing = true;
      return () => {
        observing = false;
      };
    },
    schedule: clock.schedule,
    onFound: (el) => calls.found.push(el),
    onMissing: (a) => calls.missing.push(a),
  });
  return { calls, clock, change: () => change(), observing: () => observing };
}

describe("rule 1: wait, then miss", () => {
  it("rings an anchor that appears late, and never calls onMissing", () => {
    const found = { el: null as object | null };
    const w = watch(found);
    expect(w.calls.found).toHaveLength(0);
    found.el = { id: "late" };
    w.change();
    expect(w.calls.found).toEqual([{ id: "late" }]);
    w.clock.fire();
    expect(w.calls.missing).toEqual([]);
    expect(w.clock.pending()).toBe(0);
    expect(w.observing()).toBe(false);
  });

  it("calls onMissing once when the wait ends, and stops watching", () => {
    const w = watch({ el: null });
    w.clock.fire();
    w.clock.fire();
    w.change();
    expect(w.calls.missing).toEqual(["home.ask"]);
    expect(w.calls.found).toHaveLength(0);
    expect(w.observing()).toBe(false);
  });

  it("finds an anchor already on the page with no wait", () => {
    const w = watch({ el: { id: "now" } });
    expect(w.calls.found).toHaveLength(1);
    expect(w.clock.pending()).toBe(0);
  });

  it("builds the selector with an escape, so an id cannot inject one", () => {
    const sel = anchorSelector('x"] , body [data-guide="y');
    expect(sel.startsWith('[data-guide="')).toBe(true);
    expect(sel.endsWith('"]')).toBe(true);
    // Every quote inside the id is escaped, so the value never ends early.
    expect(sel.slice(13, -2)).not.toMatch(/(^|[^\\])"/);
    expect(anchorSelector("home.ask")).toContain("home");
  });
});

describe("rule 2: ring, in a portal, that takes no pointer", () => {
  it("outlines the anchor box and follows it", () => {
    const r = { left: 100, top: 50, right: 180, bottom: 90, width: 80, height: 40 };
    expect(ringBox(r)).toEqual({ left: 96, top: 46, width: 88, height: 48 });
    expect(ringBox({ ...r, left: 140, top: 10, right: 220, bottom: 50 })).toMatchObject({ left: 136, top: 6 });
  });

  it("renders through a body portal with pointer-events none, and re-places on scroll and resize", () => {
    expect(CODE).toMatch(/createPortal\([\s\S]*document\.body/);
    expect(CODE).toMatch(/pointer-events-none fixed/);
    expect(CODE).toMatch(/addEventListener\("scroll", place, true\)/);
    expect(CODE).toMatch(/addEventListener\("resize", place\)/);
    expect(CODE).toContain("ring-primary");
  });
});

describe("rule 3: scroll into view", () => {
  it("scrolls with block nearest", () => {
    const seen: unknown[] = [];
    scrollAnchorIntoView({ scrollIntoView: (o?: unknown) => void seen.push(o) } as HTMLElement, false);
    expect(seen).toEqual([{ block: "nearest", behavior: "smooth" }]);
  });
});

describe("rule 4: focus stays on the page", () => {
  it("never moves focus, traps it, or makes the page inert", () => {
    expect(CODE).not.toMatch(/\.focus\(|autoFocus|tabIndex|inert|aria-modal|FocusTrap|focusTrap/);
  });

  it("puts only natural tab stops in the popover", () => {
    const html = renderToStaticMarkup(
      createElement(GuidePopoverBody, { say: "x", onNext: () => {}, onBack: () => {}, onEnd: () => {} }),
    );
    expect(html).not.toMatch(/tabindex|autofocus/i);
    expect(html.match(/<button/g)).toHaveLength(3);
  });
});

describe("rule 5: announce", () => {
  it("puts the line in a polite live region, and the popover has a name", () => {
    const html = renderToStaticMarkup(createElement(GuidePopoverBody, { say: "Ask here", onEnd: () => {} }));
    expect(html).toMatch(/role="status"[^>]*aria-live="polite"|aria-live="polite"[^>]*role="status"/);
    expect(html).toContain("Ask here");
    expect(CODE).toMatch(/role="group"\s+aria-label="Guide"/);
  });

  it("shows 2 of 5 in the muted token, and Next and Back only when given", () => {
    const html = renderToStaticMarkup(
      createElement(GuidePopoverBody, { say: "x", step: { index: 2, total: 5 }, onEnd: () => {} }),
    );
    expect(html).toContain("2 of 5");
    expect(html).toContain("text-muted-foreground");
    expect(html).toContain("End");
    expect(html).not.toContain("Next");
    expect(html).not.toContain("Back");
  });
});

describe("rule 6: Escape ends it in the same frame (G10)", () => {
  it("calls onEnd synchronously on Escape, with no timer, and not on other keys", () => {
    let handler: ((e: Event) => void) | null = null;
    const doc = {
      addEventListener: (_t: string, h: (e: Event) => void) => void (handler = h),
      removeEventListener: (_t: string, h: (e: Event) => void) => void (handler === h && (handler = null)),
    };
    let ended = 0;
    const off = bindEscape(doc as unknown as Document, () => void ended++);
    handler!({ key: "a" } as unknown as Event);
    expect(ended).toBe(0);
    handler!({ key: "Escape" } as unknown as Event);
    expect(ended).toBe(1);
    off();
    expect(handler).toBeNull();
  });

  it("has no exit timer or exit animation: unmount removes the ring at once", () => {
    // The one timer is the wait for the anchor, behind the scheduler.
    expect(CODE.match(/setTimeout\(/g)).toHaveLength(1);
    expect(CODE).not.toMatch(/AnimatePresence|onAnimationEnd|onTransitionEnd/);
  });
});

describe("rule 7: anchor click", () => {
  it("calls onAnchorClick when the anchor is pressed, and stops after cleanup", () => {
    let handler: (() => void) | null = null;
    const el = {
      addEventListener: (_t: string, h: () => void) => void (handler = h),
      removeEventListener: () => void (handler = null),
    };
    let n = 0;
    const off = bindAnchorClick(el as unknown as HTMLElement, () => void n++);
    handler!();
    expect(n).toBe(1);
    off();
    expect(handler).toBeNull();
  });
});

describe("rule 8: reduced motion", () => {
  const mm = (matches: boolean) => ({ matchMedia: () => ({ matches }) });

  it("reads the media query", () => {
    expect(prefersReducedMotion(mm(true))).toBe(true);
    expect(prefersReducedMotion(mm(false))).toBe(false);
    expect(prefersReducedMotion(undefined)).toBe(false);
  });

  it("has no transition class, and scrolls with auto", () => {
    expect(motionClasses(true, "left,top")).toBe("");
    expect(motionClasses(false, "left,top")).toContain("transition");
    const seen: { behavior?: string }[] = [];
    scrollAnchorIntoView(
      { scrollIntoView: (o?: unknown) => void seen.push(o as { behavior?: string }) } as HTMLElement,
      true,
    );
    expect(seen[0].behavior).toBe("auto");
  });
});

describe("rule 9: popover placement", () => {
  const vp = { width: 1280, height: 800 };
  const size = { width: 320, height: 120 };
  const at = (left: number, top: number, w = 80, h = 40) => ({
    left,
    top,
    right: left + w,
    bottom: top + h,
    width: w,
    height: h,
  });

  it("sits below the anchor, centred on it", () => {
    const p = placePopover(at(600, 100), size, vp);
    expect(p.side).toBe("below");
    expect(p.top).toBe(100 + 40 + 12);
    expect(p.left).toBe(640 - 160);
  });

  it("flips above when below has no room", () => {
    const p = placePopover(at(600, 700), size, vp);
    expect(p.side).toBe("above");
    expect(p.top).toBe(700 - 12 - 120);
  });

  it("clamps inside the window at the edges", () => {
    expect(placePopover(at(0, 100), size, vp).left).toBe(8);
    expect(placePopover(at(1200, 100), size, vp).left).toBe(1280 - 8 - 320);
  });

  it("fits at 390 px wide, and narrows on a narrower window", () => {
    const narrow = { width: 390, height: 800 };
    for (const left of [0, 150, 310]) {
      const p = placePopover(at(left, 100), size, narrow);
      expect(p.left).toBeGreaterThanOrEqual(8);
      expect(p.left + p.width).toBeLessThanOrEqual(390 - 8);
    }
    expect(placePopover(at(310, 100), size, { width: 300, height: 800 }).width).toBe(284);
  });

  it("stays inside the window on a short viewport", () => {
    const p = placePopover(at(100, 60, 80, 40), size, { width: 390, height: 200 });
    expect(p.top).toBeGreaterThanOrEqual(8);
    expect(p.top + 120).toBeLessThanOrEqual(200 - 8);
  });
});
