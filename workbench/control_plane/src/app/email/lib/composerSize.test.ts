// The size of a composer body (owner request, 2026-10-14): the pure rules of
// `composerSize.ts`. The parts that draw them are in
// `components/composerSizeUi.test.ts`.
import { describe, expect, it, vi } from "vitest";

import {
  AUTO_CAP,
  COMPOSER_HEIGHT_PREFIX,
  INITIAL_SIZE,
  KEY_STEP_PX,
  MANUAL_CAP,
  MIN_LINES,
  PAGE_STEP_PX,
  autoCapPx,
  bodySize,
  composerHeightKey,
  dragResize,
  expandedScrollTop,
  keyResize,
  manualMaxPx,
  minBodyPx,
  paneInnerHeight,
  readComposerHeight,
  sizeReducer,
  writeComposerHeight,
  type HeightStore,
  type SizeState,
} from "./composerSize";

// A body of 20 px lines with 24 px of padding: the inline reply in `text-sm`.
const LINE = 20;
const MIN = minBodyPx(LINE, 24); // 8 lines = 184
const PANE = 800; // the visible reading pane
const AUTO: SizeState = INITIAL_SIZE;
const manual = (px: number): SizeState => ({ mode: "manual", manualPx: px, expanded: false });
const measure = (contentPx: number, narrow = false) => ({ contentPx, minPx: MIN, paneHeight: PANE, narrow });

describe("composer-size: auto-grow", () => {
  it("never shows fewer than MIN_LINES lines", () => {
    expect(MIN_LINES).toBe(8);
    expect(MIN).toBe(8 * LINE + 24);
    // An empty body and a two-line body both show the minimum.
    expect(bodySize(AUTO, measure(44))).toEqual({ heightPx: MIN, scrolls: false });
    expect(bodySize(AUTO, measure(0))).toEqual({ heightPx: MIN, scrolls: false });
  });

  it("grows with the text, with no inner scrollbar, up to the cap", () => {
    expect(bodySize(AUTO, measure(300))).toEqual({ heightPx: 300, scrolls: false });
    expect(bodySize(AUTO, measure(400.4))).toEqual({ heightPx: 401, scrolls: false });
  });

  it("stops at the cap of the pane, and then scrolls inside", () => {
    const cap = autoCapPx(PANE, MIN);
    expect(cap).toBe(Math.round(PANE * AUTO_CAP));
    expect(bodySize(AUTO, measure(cap))).toEqual({ heightPx: cap, scrolls: false });
    expect(bodySize(AUTO, measure(2000))).toEqual({ heightPx: cap, scrolls: true });
  });

  it("takes the cap from the pane, not from a fixed number", () => {
    const small = bodySize(AUTO, { ...measure(2000), paneHeight: 500 });
    const tall = bodySize(AUTO, { ...measure(2000), paneHeight: 1200 });
    expect(small.heightPx).toBe(300);
    expect(tall.heightPx).toBe(720);
  });

  it("keeps the minimum on a pane too short for the cap", () => {
    expect(autoCapPx(200, MIN)).toBe(MIN);
    expect(bodySize(AUTO, { ...measure(2000), paneHeight: 200 })).toEqual({ heightPx: MIN, scrolls: true });
  });
});

describe("composer-size: manual height", () => {
  it("holds the dragged height, and scrolls only when the text is taller", () => {
    expect(bodySize(manual(400), measure(100))).toEqual({ heightPx: 400, scrolls: false });
    expect(bodySize(manual(400), measure(900))).toEqual({ heightPx: 400, scrolls: true });
  });

  it("clamps to the minimum and to MANUAL_CAP of the pane", () => {
    expect(manualMaxPx(PANE, MIN)).toBe(Math.round(PANE * MANUAL_CAP));
    expect(bodySize(manual(50), measure(100)).heightPx).toBe(MIN);
    expect(bodySize(manual(5000), measure(100)).heightPx).toBe(720);
  });

  it("does not apply on a narrow window, where there is no grip", () => {
    expect(bodySize(manual(600), measure(300, true))).toEqual({ heightPx: 300, scrolls: false });
  });

  it("leaves room for the rest of the composer, so Send stays in the pane", () => {
    // A height dragged on a big screen, read back on a laptop: a 517 px pane
    // with 300 px of header, recipients, AI panel and footer.
    expect(manualMaxPx(517, MIN, 300)).toBe(217);
    expect(bodySize(manual(700), { ...measure(100), paneHeight: 517, chromePx: 300 }).heightPx).toBe(217);
    // The chrome never pushes the body below its minimum.
    expect(manualMaxPx(517, MIN, 450)).toBe(MIN);
    // With little chrome, MANUAL_CAP still holds.
    expect(manualMaxPx(PANE, MIN, 40)).toBe(720);
  });

  it("drags between the limits", () => {
    expect(dragResize(300, 40.6, MIN, 720)).toBe(341);
    expect(dragResize(300, -500, MIN, 720)).toBe(MIN);
    expect(dragResize(300, 5000, MIN, 720)).toBe(720);
  });
});

describe("composer-size: the reducer", () => {
  it("loads a kept height as manual, and ignores no height", () => {
    expect(sizeReducer(AUTO, { type: "load", px: 420 })).toEqual(manual(420));
    expect(sizeReducer(AUTO, { type: "load", px: null })).toBe(AUTO);
    expect(sizeReducer(AUTO, { type: "load", px: 0 })).toBe(AUTO);
  });

  it("a resize sets manual, and a reset (double-click) goes back to auto", () => {
    const dragged = sizeReducer(AUTO, { type: "resize", px: 512.4 });
    expect(dragged).toEqual(manual(512));
    expect(sizeReducer(dragged, { type: "reset" })).toEqual(AUTO);
  });

  it("Expand and Collapse return to the mode from before", () => {
    const dragged = manual(512);
    const big = sizeReducer(dragged, { type: "toggleExpand" });
    expect(big).toEqual({ ...dragged, expanded: true });
    expect(sizeReducer(big, { type: "toggleExpand" })).toEqual(dragged);
    const auto = sizeReducer(sizeReducer(AUTO, { type: "toggleExpand" }), { type: "toggleExpand" });
    expect(auto).toEqual(AUTO);
  });

  it("a close ends the expanded mode and keeps the height", () => {
    const big = { ...manual(512), expanded: true };
    expect(sizeReducer(big, { type: "close" })).toEqual(manual(512));
    expect(sizeReducer(AUTO, { type: "close" })).toBe(AUTO);
  });
});

describe("composer-size: the keys of the grip", () => {
  it("steps with the arrows, and ArrowDown makes the body taller", () => {
    expect(keyResize("ArrowDown", 300, MIN, 720)).toBe(300 + KEY_STEP_PX);
    expect(keyResize("ArrowUp", 300, MIN, 720)).toBe(300 - KEY_STEP_PX);
    expect(keyResize("PageDown", 300, MIN, 720)).toBe(300 + PAGE_STEP_PX);
    expect(keyResize("PageUp", 300, MIN, 720)).toBe(MIN);
  });

  it("stays between the limits, and Home and End go to them", () => {
    expect(keyResize("ArrowUp", MIN, MIN, 720)).toBe(MIN);
    expect(keyResize("ArrowDown", 710, MIN, 720)).toBe(720);
    expect(keyResize("Home", 500, MIN, 720)).toBe(MIN);
    expect(keyResize("End", 500, MIN, 720)).toBe(720);
  });

  it("leaves every other key alone, Escape too", () => {
    for (const key of ["Escape", "Enter", "Tab", "a", "ArrowLeft"]) {
      expect(keyResize(key, 300, MIN, 720)).toBeNull();
    }
  });
});

/** A `localStorage` in memory. */
function memoryStore(): HeightStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
}

/** A `localStorage` that throws on each call, as in a locked-down browser. */
const throwingStore: HeightStore = {
  getItem: () => { throw new Error("SecurityError"); },
  setItem: () => { throw new Error("QuotaExceededError"); },
  removeItem: () => { throw new Error("SecurityError"); },
};

describe("composer-size: the kept height, per member", () => {
  const ME = "me@example.com|org-1";
  const OTHER = "other@example.com|org-1";

  it("keys the height by the member scope, and needs one", () => {
    expect(composerHeightKey(ME)).toBe(`${COMPOSER_HEIGHT_PREFIX}${ME}`);
    expect(composerHeightKey(null)).toBeNull();
    expect(composerHeightKey("anonymous")).toBeNull();
  });

  it("writes and reads a height, and one member never reads another's", () => {
    const store = memoryStore();
    writeComposerHeight(ME, 432.6, store);
    expect(store.data.get(`${COMPOSER_HEIGHT_PREFIX}${ME}`)).toBe("433");
    expect(readComposerHeight(ME, store)).toBe(433);
    expect(readComposerHeight(OTHER, store)).toBeNull();
  });

  it("forgets the height on a reset", () => {
    const store = memoryStore();
    writeComposerHeight(ME, 400, store);
    writeComposerHeight(ME, null, store);
    expect(store.data.size).toBe(0);
    expect(readComposerHeight(ME, store)).toBeNull();
  });

  it("reads no height from junk", () => {
    const store = memoryStore();
    store.setItem(`${COMPOSER_HEIGHT_PREFIX}${ME}`, "tall");
    expect(readComposerHeight(ME, store)).toBeNull();
    store.setItem(`${COMPOSER_HEIGHT_PREFIX}${ME}`, "-20");
    expect(readComposerHeight(ME, store)).toBeNull();
  });

  it("survives a storage that throws, so the composer still works", () => {
    expect(() => writeComposerHeight(ME, 400, throwingStore)).not.toThrow();
    expect(() => writeComposerHeight(ME, null, throwingStore)).not.toThrow();
    expect(readComposerHeight(ME, throwingStore)).toBeNull();
    // With no storage at all, nothing is kept and nothing throws.
    expect(readComposerHeight(ME, null)).toBeNull();
    expect(() => writeComposerHeight(ME, 400, null)).not.toThrow();
  });

  it("survives a window whose localStorage getter throws", async () => {
    const win = {};
    Object.defineProperty(win, "localStorage", { get: () => { throw new Error("SecurityError"); } });
    vi.stubGlobal("window", win);
    try {
      const { browserStore } = await import("./composerSize");
      expect(browserStore()).toBeNull();
      expect(readComposerHeight(ME)).toBeNull();
      expect(() => writeComposerHeight(ME, 400)).not.toThrow();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("composer-size: the expanded composer fits inside the pane's padding", () => {
  it("measures the pane inside its padding", () => {
    // The thread is `py-5`: 20 px at each end.
    expect(paneInnerHeight(760, 20, 20)).toBe(720);
    expect(paneInnerHeight(30, 20, 20)).toBe(0);
  });

  it("scrolls so the composer's top sits at the top padding", () => {
    // The composer top is 300 px below the port top, and the pane holds 20 px
    // of padding: the pane scrolls 280 px further.
    expect(expandedScrollTop(100, 400, 100, 20)).toBe(380);
    // A composer above the port scrolls back up.
    expect(expandedScrollTop(500, 50, 100, 20)).toBe(430);
    // Never below 0.
    expect(expandedScrollTop(0, 110, 100, 20)).toBe(0);
  });
});
