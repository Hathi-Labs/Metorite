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
  clampRing,
  placePopover,
  prefersReducedMotion,
  ringBox,
  scrollAnchorIntoView,
  visibleRect,
  watchAnchor,
  watchDetached,
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

describe("rule 2b: the ring stays inside the window", () => {
  const vp = { width: 1280, height: 900 };

  it("leaves a ring that fits as it is", () => {
    const box = ringBox({ left: 100, top: 50, right: 180, bottom: 90, width: 80, height: 40 });
    expect(clampRing(box, vp)).toEqual(box);
  });

  it("holds a ring taller than the window to the window, inset so the whole line shows", () => {
    // My Day was 1555 px tall in a 900 px window.
    const tall = ringBox({ left: 300, top: 40, right: 1100, bottom: 1595, width: 800, height: 1555 });
    const c = clampRing(tall, vp);
    expect(c.top).toBeGreaterThanOrEqual(4);
    expect(c.top + c.height).toBeLessThanOrEqual(900 - 4);
    expect(c.left).toBe(tall.left);
  });

  it("holds a ring that starts at the window edge, or fills the window (390 px)", () => {
    const phone = { width: 390, height: 852 };
    const sheet = clampRing(ringBox({ left: 0, top: 0, right: 390, bottom: 852, width: 390, height: 852 }), phone);
    expect(sheet).toEqual({ left: 4, top: 4, width: 382, height: 844 });
    const edge = clampRing(ringBox({ left: 0, top: 100, right: 256, bottom: 500, width: 256, height: 400 }), phone);
    expect(edge.left).toBe(4);
    expect(edge.left + edge.width).toBeLessThanOrEqual(386);
  });

  it("never gives a negative size for an anchor wholly off screen", () => {
    const c = clampRing(ringBox({ left: 2000, top: 2000, right: 2080, bottom: 2040, width: 80, height: 40 }), vp);
    expect(c.width).toBe(0);
    expect(c.height).toBe(0);
  });

  it("cuts the anchor to the window for the popover", () => {
    const v = visibleRect({ left: 300, top: 40, right: 1100, bottom: 1595, width: 800, height: 1555 }, vp);
    expect([v.top, v.bottom, v.height]).toEqual([40, 900, 860]);
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
  it("holds NO live region: GuideHost owns the one that announces each step at every width", () => {
    const html = renderToStaticMarkup(createElement(GuidePopoverBody, { say: "Ask here", onEnd: () => {} }));
    expect(html).toContain("Ask here");
    expect(html).not.toMatch(/aria-live|role="status"|aria-hidden/);
    expect(CODE).not.toMatch(/aria-live|role="status"/);
    // The popover keeps a name.
    expect(CODE).toMatch(/role="group"\s+aria-label="Guide"/);
    // The host holds exactly one live region, in the tree for every width.
    const host = readFileSync(join(__dirname, "GuideHost.tsx"), "utf-8").replace(/\r\n/g, "\n");
    expect(host.match(/aria-live="polite"/g)).toHaveLength(1);
    expect(host).toContain("data-guide-live");
    // The step card and the strip add none.
    for (const f of ["GuideStepCard.tsx", "AssistantDock.tsx"]) {
      const src = readFileSync(join(__dirname, f), "utf-8");
      const guide = f === "GuideStepCard.tsx" ? src : src.slice(src.indexOf("data-guide-strip"), src.indexOf("data-guide-strip") + 900);
      expect(guide).not.toMatch(/aria-live="polite"/);
    }
  });

  it("labels the end button End tour, as the card and the strip do", () => {
    const html = renderToStaticMarkup(createElement(GuidePopoverBody, { say: "x", onEnd: () => {} }));
    expect(html).toContain("End tour");
    expect(readFileSync(join(__dirname, "AssistantDock.tsx"), "utf-8")).toContain("End tour");
    expect(readFileSync(join(__dirname, "GuideStepCard.tsx"), "utf-8")).toContain("End tour");
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

  it("has no transition at all, so the ring follows its anchor with no lag", () => {
    // The old file built `transition-[${props}]` at run time. Tailwind never
    // generated that class, so `duration-150` alone gave `transition: all`.
    // Nothing here moves by transition: no class, no style, no template.
    expect(CODE).not.toMatch(/transition(?!end)|duration-|ease-|animate-|animation(?!end)|motion-safe|motion-reduce/i);
    expect(CODE).not.toMatch(/motionClasses/);
    // A class is never built at run time from a variable, whatever its name.
    const classNames = [...CODE.matchAll(/className=\{`([^`]*)`\}/g)].map((m) => m[1]);
    for (const c of classNames) {
      for (const hole of c.matchAll(/\$\{([^}]*)\}/g)) expect(hole[1]).toMatch(/^PANEL_(SURFACES|LAYERS)\.\w+$/);
    }
    // The ring takes no pointer. Both parts sit on the design system's top layer, and write no z value.
    expect(CODE).toMatch(/data-guide-ring=""[\s\S]*?className=\{`pointer-events-none fixed \$\{PANEL_LAYERS\.top\} [^`$]*`\}/);
    expect(CODE).not.toMatch(/z-\[|z-\d/);
  });

  it("scrolls with auto under reduced motion", () => {
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

  it("sits beside a tall anchor: right of a left sidebar, left of a right panel", () => {
    // A sidebar: 256 wide, the full height. The popover goes to its right and clears it.
    const sidebar = at(0, 0, 256, 800);
    const r = placePopover(sidebar, size, vp);
    expect(r.side).toBe("right");
    expect(r.left).toBeGreaterThanOrEqual(256);
    // The side panel: 400 wide at the right edge. The popover goes to its left.
    const panel = at(880, 0, 400, 800);
    const l = placePopover(panel, size, vp);
    expect(l.side).toBe("left");
    expect(l.left + l.width).toBeLessThanOrEqual(880);
    // Neither covers its anchor.
    expect(r.top).toBeGreaterThanOrEqual(8);
    expect(r.top + 120).toBeLessThanOrEqual(800 - 8);
  });

  it("sits beside an anchor when neither below nor above has room", () => {
    const wide = at(100, 30, 200, 740);
    expect(placePopover(wide, size, vp).side).toBe("right");
  });

  it("falls back to below or above when the window has no room beside a tall anchor", () => {
    const narrow = { width: 390, height: 800 };
    const sheet = at(0, 100, 390, 500);
    const p = placePopover(sheet, size, narrow);
    expect(["below", "above"]).toContain(p.side);
    expect(p.left).toBeGreaterThanOrEqual(8);
    expect(p.left + p.width).toBeLessThanOrEqual(390 - 8);
  });

  it("stays inside the window on a short viewport", () => {
    const p = placePopover(at(100, 60, 80, 40), size, { width: 390, height: 200 });
    expect(p.top).toBeGreaterThanOrEqual(8);
    expect(p.top + 120).toBeLessThanOrEqual(200 - 8);
  });
});

describe("rule 10: a ringed anchor that leaves the page", () => {
  function rig(gone: { v: boolean }) {
    let change: () => void = () => {};
    let observing = false;
    const calls = { gone: 0 };
    const cancel = watchDetached({
      isGone: () => gone.v,
      observe: (cb) => {
        change = cb;
        observing = true;
        return () => {
          observing = false;
        };
      },
      onGone: () => void calls.gone++,
    });
    return { calls, cancel, change: () => change(), observing: () => observing };
  }

  it("does nothing while the anchor stays, and calls onGone once when it leaves", () => {
    const gone = { v: false };
    const w = rig(gone);
    w.change();
    expect(w.calls.gone).toBe(0);
    expect(w.observing()).toBe(true);
    gone.v = true;
    w.change();
    w.change();
    expect(w.calls.gone).toBe(1);
    expect(w.observing()).toBe(false);
  });

  it("stops watching on cancel", () => {
    const gone = { v: false };
    const w = rig(gone);
    w.cancel();
    gone.v = true;
    w.change();
    expect(w.calls.gone).toBe(0);
    expect(w.observing()).toBe(false);
  });

  it("starts the wait again when the anchor goes, so a miss is a missing step", () => {
    expect(CODE).toMatch(/watchDetached\(\{[\s\S]*?isGone: \(\) => !el\.isConnected \|\| !isShown\(el\)/);
    expect(CODE).toMatch(/setAttempt\(\(n\) => n \+ 1\)/);
    expect(CODE).toMatch(/\[anchor, waitMs, attempt\]/);
  });
});
