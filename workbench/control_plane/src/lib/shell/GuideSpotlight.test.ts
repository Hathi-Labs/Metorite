/**
 * The guide spotlight, one test per access rule (`assistant_guide.md` §5.3).
 *
 * `vitest.config.ts` is `environment: "node"` and has no DOM library. As in
 * `InfoTip.test.ts`, each rule is a pure function or an injected controller,
 * driven here with fakes, and the markup is read with `renderToStaticMarkup`.
 * The rules that need a live browser (focus, Tab) are fenced on the source and
 * walked by the GD-4 e2e.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  GuideCardBody,
  anchorSelector,
  bindAnchorClick,
  bindEscape,
  clampRing,
  dimHoles,
  dockEdge,
  placeCard,
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
    const host = readFileSync(join(__dirname, "GuideHost.tsx"), "utf-8");
    expect(host).toMatch(/createPortal\(\s*<>[\s\S]*?\{dim\}[\s\S]*?document\.body/);
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

  it("cuts the anchor to the window for the card", () => {
    const v = visibleRect({ left: 300, top: 40, right: 1100, bottom: 1595, width: 800, height: 1555 }, vp);
    expect([v.top, v.bottom, v.height]).toEqual([40, 900, 860]);
  });
});

function fakeScroller(seen: { top: number; behavior?: string }[]) {
  // A port from y=100 to y=500, scrolled to 0. A target at 600 to 640 sits below it.
  return {
    scrollTop: 0,
    scrollHeight: 2000,
    clientTop: 0,
    clientHeight: 400,
    getBoundingClientRect: () => ({ top: 100 }),
    scrollTo: (o: { top: number; behavior?: ScrollBehavior }) => void seen.push({ top: o.top, behavior: o.behavior }),
  };
}
const BELOW = { getBoundingClientRect: () => ({ top: 600, bottom: 640 }) };

describe("rule 3: scroll into view", () => {
  it("scrolls ONE scroller by the least distance, as block nearest does", () => {
    const seen: { top: number; behavior?: string }[] = [];
    scrollAnchorIntoView(BELOW, false, fakeScroller(seen));
    // The target's bottom (640) meets the port's bottom (500): 140 down.
    expect(seen).toEqual([{ top: 140, behavior: "smooth" }]);
  });

  it("moves nothing when no box above the anchor scrolls", () => {
    expect(() => scrollAnchorIntoView(BELOW, false, null)).not.toThrow();
  });

  it("never calls scrollIntoView, which moves the app shell too (scrollWithin.test.ts)", () => {
    expect(CODE).not.toMatch(/\.scrollIntoView\s*\(/);
    expect(CODE).toMatch(/!p\.hasAttribute\("data-app-shell"\)/);
  });
});

describe("rule 4: focus stays on the page", () => {
  it("never moves focus, traps it, or makes the page inert", () => {
    expect(CODE).not.toMatch(/\.focus\(|autoFocus|tabIndex|inert|aria-modal|FocusTrap|focusTrap/);
  });

  it("puts only natural tab stops in the card", () => {
    const html = renderToStaticMarkup(
      createElement(GuideCardBody, { say: "x", onNext: () => {}, onBack: () => {}, onEnd: () => {} }),
    );
    expect(html).not.toMatch(/tabindex|autofocus/i);
    expect(html.match(/<button/g)).toHaveLength(3);
  });
});

describe("rule 5: announce", () => {
  it("holds NO live region: GuideHost owns the one that announces each step at every width", () => {
    const html = renderToStaticMarkup(createElement(GuideCardBody, { say: "Ask here", onEnd: () => {} }));
    expect(html).toContain("Ask here");
    expect(html).not.toMatch(/aria-live|role="status"|aria-hidden/);
    expect(CODE).not.toMatch(/aria-live|role="status"/);
    // The card keeps a name.
    expect(CODE).toMatch(/role="group"\s+aria-label="Guide"/);
    // The host holds exactly one live region, in the tree for every width.
    const host = readFileSync(join(__dirname, "GuideHost.tsx"), "utf-8").replace(/\r\n/g, "\n");
    expect(host.match(/aria-live="polite"/g)).toHaveLength(1);
    expect(host).toContain("data-guide-live");
    // The chat draws nothing for the tour, so the offer chip adds none.
    expect(readFileSync(join(__dirname, "GuideOffer.tsx"), "utf-8")).not.toMatch(/aria-live="polite"/);
  });

  it("labels the end button End tour", () => {
    const html = renderToStaticMarkup(createElement(GuideCardBody, { say: "x", onEnd: () => {} }));
    expect(html).toContain("End tour");
  });

  it("shows 2 of 5 in the muted token, and Next and Back only when given", () => {
    const html = renderToStaticMarkup(
      createElement(GuideCardBody, { say: "x", step: { index: 2, total: 5 }, onEnd: () => {} }),
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
    const seen: { top: number; behavior?: string }[] = [];
    scrollAnchorIntoView(BELOW, true, fakeScroller(seen));
    expect(seen[0].behavior).toBe("auto");
  });
});

describe("rule 9: card placement", () => {
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

  it("desktop: the card sits LEFT of the assistant panel (stop 3) and does not overlap it", () => {
    const desk = { width: 1440, height: 900 };
    const panel = at(1040, 60, 380, 780);
    const c = placeCard(panel, size, desk, false);
    expect(c.side).toBe("left");
    expect(c.left + c.width).toBeLessThanOrEqual(1040);
    expect(c.left).toBeGreaterThanOrEqual(8);
    expect(c.top).toBeGreaterThanOrEqual(8);
    expect(c.top + 120).toBeLessThanOrEqual(900 - 8);
  });

  it("desktop with no anchor: centred at the foot of the window", () => {
    const desk = { width: 1440, height: 900 };
    const c = placeCard(null, size, desk, false);
    expect(c).toMatchObject({ side: "bottom", left: 560, top: 900 - 8 - 120, width: 320 });
  });

  it("phone: the dock edge is the one FARTHER from the anchor", () => {
    const ph = { width: 390, height: 800 };
    // Centre in the lower half: the top edge. Centre in the upper half: the bottom edge.
    expect(dockEdge(at(16, 700, 358, 60), ph)).toBe("top");
    expect(dockEdge(at(16, 100, 358, 60), ph)).toBe("bottom");
    expect(dockEdge(at(16, 380, 358, 40), ph)).toBe("bottom");
    expect(dockEdge(at(16, 381, 358, 40), ph)).toBe("top");
    // No anchor: the top, clear of the message field.
    expect(dockEdge(null, ph)).toBe("top");
    // An anchor that fills the window (the phone sheet) leaves no free edge: the top keeps the message field clear.
    expect(dockEdge(at(0, 0, 390, 800), ph)).toBe("top");
    expect(dockEdge(at(0, 0, 390, 479), ph)).toBe("bottom");
    expect(dockEdge(at(0, 0, 390, 480), ph)).toBe("top");
  });

  it("phone: full width less the 16 px gutter, docked inside the window, clear of the anchor", () => {
    const ph = { width: 390, height: 800 };
    const field = at(16, 724, 358, 60);
    const top = placeCard(field, size, ph, true);
    expect(top).toMatchObject({ side: "top", left: 16, top: 16, width: 358 });
    expect(top.top + 120).toBeLessThanOrEqual(field.top);
    const bar = at(16, 8, 358, 48);
    const bottom = placeCard(bar, size, ph, true);
    expect(bottom).toMatchObject({ side: "bottom", left: 16, top: 800 - 16 - 120, width: 358 });
    expect(bottom.top).toBeGreaterThanOrEqual(bar.bottom);
    for (const c of [top, bottom]) {
      expect(c.left + c.width).toBeLessThanOrEqual(390 - 16);
      expect(c.top + 120).toBeLessThanOrEqual(800);
    }
  });

  it("phone: a card taller than the window keeps its top inside the window", () => {
    const c = placeCard(at(16, 8, 358, 48), { width: 320, height: 2000 }, { width: 390, height: 800 }, true);
    expect(c.top).toBeGreaterThanOrEqual(16);
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

describe("rule 11: the dim layer (owner request 2026-11-03)", () => {
  const vp = { width: 1440, height: 900 };
  const rect = (left: number, top: number, width: number, height: number) => ({
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
  });
  const anchor = rect(200, 100, 300, 400);

  it("has ONE hole, the anchor, with the ring's box", () => {
    const holes = dimHoles(anchor, vp);
    expect(holes).toHaveLength(1);
    expect(holes[0]).toMatchObject({ left: 196, top: 96, width: 308, height: 408, r: 8 });
  });

  it("clamps the hole to the window", () => {
    const [a] = dimHoles(rect(-50, -20, 2000, 3000), vp);
    expect(a).toMatchObject({ left: 4, top: 4, width: 1432, height: 892 });
  });

  it("has no hole when the anchor is missing, so the dim covers the window under the card", () => {
    expect(dimHoles(null, vp)).toEqual([]);
  });

  it("has no carrier logic: the card sits above the dim and needs no hole", () => {
    expect(CODE).not.toMatch(/findCarrier|DimCarrier|data-guide-strip/);
  });

  it("fills with the one scrim token, takes no pointer, and has no transition", () => {
    // DESIGN_SYSTEM.md section 4a: the fill is `bg-background/80` on a box, `fill-background/80` on a mask rect.
    expect(CODE).toMatch(/className="fill-background\/80"/);
    expect(CODE).not.toMatch(/bg-black|fill-black|bg-\[|#[0-9a-f]{3,6}/i);
    expect(CODE).toMatch(/data-guide-dim=""[\s\S]*?className=\{`pointer-events-none fixed inset-0 \$\{PANEL_LAYERS\.top\}/);
    expect(CODE).toMatch(/<mask[\s\S]*<rect[^>]*fill="white"[\s\S]*data-guide-hole/);
  });

  it("puts the dim, the ring and the card in ONE portal, the dim first, so the card paints over it", () => {
    const host = readFileSync(join(__dirname, "GuideHost.tsx"), "utf-8").replace(/\r\n/g, "\n");
    // One portal holds the three. The dim comes before the spotlight (ring + card) and the missing card.
    const at = host.indexOf("{dim}\n");
    expect(at).toBeGreaterThan(0);
    expect(at).toBeLessThan(host.indexOf("{spot}\n"));
    expect(host.indexOf("{spot}\n")).toBeLessThan(host.indexOf("{missingCard}"));
    expect(host.match(/createPortal\(/g)).toHaveLength(2);
    // The dim and the spotlight draw no portal of their own.
    expect(CODE).not.toMatch(/createPortal\(/);
  });

  it("draws the card on the top layer with the accent edge and the panel surface", () => {
    expect(CODE).toMatch(/data-guide-card=""[\s\S]*?\$\{PANEL_LAYERS\.top\} p-3 outline outline-2 outline-primary \$\{PANEL_SURFACES\.panel\}/);
  });

  it("the chat and the dock draw nothing for the tour", () => {
    for (const f of ["AssistantDock.tsx", "GuideOffer.tsx"]) {
      const src = readFileSync(join(__dirname, f), "utf-8");
      expect(src).not.toMatch(/GuideStepCard|data-guide-strip|data-guide-card|guideView|tourView|nextLabel|tourChips/);
    }
    expect(existsSync(join(__dirname, "GuideStepCard.tsx"))).toBe(false);
  });
});
