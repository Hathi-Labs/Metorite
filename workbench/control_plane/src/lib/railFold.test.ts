/**
 * The My Tasks lists rail folds below `lg`, and keeps the member's choice
 * inside a band. See `railFold.ts` for the measurement that asked for it.
 */

import { describe, expect, it } from "vitest";

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  RAIL_INITIAL,
  RAIL_WIDE_QUERY,
  railClass,
  railOnBand,
  railStart,
  railToggle,
} from "./railFold";

const HERE = fileURLToPath(new URL(".", import.meta.url));

describe("the rail does not flash open on a tablet load (continuity P3)", () => {
  it("before mount, CSS hides the rail below lg and shows it at lg", () => {
    expect(railClass(false)).toBe("hidden lg:block");
  });

  it("after mount the state decides, so CSS adds nothing", () => {
    expect(railClass(true)).toBe("");
  });

  it("the hook starts unsettled and settles only in the mount effect", () => {
    const src = readFileSync(join(HERE, "railFold.ts"), "utf8");
    const hook = src.slice(src.indexOf("export function useRailFold"));
    expect(hook).toMatch(/useState\(false\)/);
    const effect = hook.slice(hook.indexOf("useEffect("));
    expect(effect).toMatch(/setSettled\(true\)/);
    expect(hook.slice(0, hook.indexOf("useEffect("))).not.toMatch(/setSettled\(true\)/);
  });

  it("the My Tasks rail wears railClass(lists.settled)", () => {
    const page = readFileSync(join(HERE, "../app/tasks/page.tsx"), "utf8");
    expect(page).toMatch(/<aside className=\{`[^`]*\$\{railClass\(lists\.settled\)\}`\}/);
  });
});

describe("the rail folds itself on tablet widths", () => {
  it("starts folded below lg and open at or above it", () => {
    expect(railStart("narrow").open).toBe(false);
    expect(railStart("wide").open).toBe(true);
  });

  it("the band edge is Tailwind's lg", () => {
    expect(RAIL_WIDE_QUERY).toBe("(min-width: 1024px)");
  });

  it("the member's choice holds while the width stays in its band", () => {
    const opened = railToggle(railStart("narrow"));
    expect(opened.open).toBe(true);
    // A resize inside the band is not a reason to undo what they chose.
    expect(railOnBand(opened, "narrow")).toEqual(opened);
    const closed = railToggle(railStart("wide"));
    expect(railOnBand(closed, "wide").open).toBe(false);
  });

  it("crossing into the other band applies that band's default", () => {
    const opened = railToggle(railStart("narrow"));
    expect(railOnBand(opened, "wide")).toEqual({ open: true, band: "wide" });
    const closed = railToggle(railStart("wide"));
    expect(railOnBand(closed, "narrow")).toEqual({ open: false, band: "narrow" });
    // And back up: a rail the member folded on a desktop comes back open
    // after a trip through the tablet band.
    expect(railOnBand(railOnBand(closed, "narrow"), "wide").open).toBe(true);
  });
});

describe("the first render does not read the window (no hydration mismatch)", () => {
  it("the initial state is the wide default, whatever the band", () => {
    expect(RAIL_INITIAL).toEqual({ open: true, band: "wide" });
  });

  it("useState takes RAIL_INITIAL, and no initializer reads matchMedia", () => {
    const src = readFileSync(join(HERE, "railFold.ts"), "utf8");
    const hook = src.slice(src.indexOf("export function useRailFold"));
    expect(hook).toMatch(/useState<RailState>\(RAIL_INITIAL\)/);
    // A lazy initializer, or any window read outside the mount effect.
    const beforeEffect = hook.slice(0, hook.indexOf("useEffect("));
    expect(beforeEffect).not.toMatch(/matchMedia|currentBand|window/);
    const initial = src.slice(src.indexOf("export const RAIL_INITIAL"));
    expect(initial.slice(0, initial.indexOf(";"))).toBe(
      'export const RAIL_INITIAL: RailState = railStart("wide")',
    );
  });
});
