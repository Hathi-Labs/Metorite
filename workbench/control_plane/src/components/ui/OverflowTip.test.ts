/**
 * The whole-name tip (owner, 2026-10-10). `vitest.config.ts` is
 * `environment: "node"`, so the behaviour is a pure reducer and the source
 * is read as text. `e2e/rail-rows.spec.ts` measures the real tip.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { PANEL_SURFACES } from "./AnchoredPanel";
import {
  OVERFLOW_TIP_DELAY_MS,
  OVERFLOW_TIP_MAX_WIDTH,
  TIP_IDLE,
  isTruncated,
  overflowTipReducer,
  overflowTipWidth,
} from "./OverflowTip";

const waiting = { phase: "waiting" as const };
const open = { phase: "open" as const };

describe("overflowTipReducer", () => {
  it("waits on enter, and never opens on enter alone", () => {
    expect(overflowTipReducer(TIP_IDLE, { type: "enter" })).toEqual(waiting);
    expect(overflowTipReducer(open, { type: "enter" })).toEqual(open);
  });

  it("opens after the delay ONLY when the label is cut", () => {
    expect(overflowTipReducer(waiting, { type: "elapsed", truncated: true })).toEqual(open);
    expect(overflowTipReducer(waiting, { type: "elapsed", truncated: false })).toEqual(TIP_IDLE);
  });

  it("does not open when the pointer left before the delay ended", () => {
    const left = overflowTipReducer(waiting, { type: "leave" });
    expect(left).toEqual(TIP_IDLE);
    expect(overflowTipReducer(left, { type: "elapsed", truncated: true })).toEqual(TIP_IDLE);
  });

  it("hides on leave and on a dismiss (Escape, a press, a scroll)", () => {
    expect(overflowTipReducer(open, { type: "leave" })).toEqual(TIP_IDLE);
    expect(overflowTipReducer(open, { type: "dismiss" })).toEqual(TIP_IDLE);
    expect(overflowTipReducer(waiting, { type: "dismiss" })).toEqual(TIP_IDLE);
  });
});

describe("the measurements", () => {
  it("calls a label cut only when its text is wider than its box", () => {
    expect(isTruncated({ scrollWidth: 240, clientWidth: 180 })).toBe(true);
    expect(isTruncated({ scrollWidth: 180, clientWidth: 180 })).toBe(false);
    expect(isTruncated({ scrollWidth: 20, clientWidth: 180 })).toBe(false);
    expect(isTruncated(null)).toBe(false);
  });

  it("waits long enough that crossing the rail lights nothing", () => {
    expect(OVERFLOW_TIP_DELAY_MS).toBeGreaterThanOrEqual(300);
    expect(OVERFLOW_TIP_DELAY_MS).toBeLessThanOrEqual(700);
  });

  it("keeps the tip inside a 390 px window", () => {
    expect(overflowTipWidth(60, 1440)).toBe(OVERFLOW_TIP_MAX_WIDTH);
    expect(overflowTipWidth(100, 390)).toBe(282);
    expect(overflowTipWidth(380, 390)).toBe(120);
  });
});

describe("the tip's markup", () => {
  const src = readFileSync(fileURLToPath(new URL("./OverflowTip.tsx", import.meta.url)), "utf8");
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

  it("is a tooltip on the tip surface, portalled by AnchoredPanel", () => {
    expect(code).toMatch(/role: "tooltip"/);
    expect(code).toMatch(/variant="tip"/);
    expect(code).toContain("<AnchoredPanel");
  });

  it("paints on the top layer, above the phone drawer", () => {
    // The drawer is `fixed inset-0 z-[70]`. The default popover layer is
    // `z-[60]`, so a tip in a drawer rail drew under it.
    expect(code).toMatch(/layer="top"/);
    const panel = readFileSync(fileURLToPath(new URL("./AnchoredPanel.tsx", import.meta.url)), "utf8");
    expect(panel).toMatch(/top: "z-\[90\]"/);
  });

  it("adds no native title and no second accessible name", () => {
    expect(code).not.toMatch(/\stitle=/);
    expect(code).not.toContain("aria-describedby");
  });

  it("draws the dark tooltip surface, takes no pointer, and is not a popup", () => {
    expect(PANEL_SURFACES.tip).toContain("bg-tooltip");
    expect(PANEL_SURFACES.tip).toContain("text-tooltip-foreground");
    expect(PANEL_SURFACES.tip).toContain("pointer-events-none");
    const panel = readFileSync(fileURLToPath(new URL("./AnchoredPanel.tsx", import.meta.url)), "utf8");
    // The outside-click marker is the panel's alone. On a tip it would tell
    // InfoTip's Escape rule that another popup is open.
    expect(panel).toMatch(/variant === "panel" \? \{ \[PREVENT_OUTSIDE_CLICK\]: "" \} : \{\}/);
  });
});
