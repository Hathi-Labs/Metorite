import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { dragTilt, edgePose, MAX_TILT_DEG, revealOf, stackBanner, SWIPE_STEP_PX } from "./cardStack";

describe("the card stack (owner, 2026-10-24)", () => {
  it("a drag tilts the card one degree per 12 px, capped, and to the side of the drag", () => {
    expect(dragTilt(0)).toBe(0);
    expect(dragTilt(-24)).toBe(-2);
    expect(dragTilt(36)).toBe(3);
    expect(dragTilt(-1000)).toBe(-MAX_TILT_DEG);
    expect(dragTilt(Number.NaN)).toBe(0);
  });

  it("the card behind is fully up at one swipe step", () => {
    expect(revealOf(0)).toBe(0);
    expect(revealOf(SWIPE_STEP_PX / 2)).toBe(0.5);
    expect(revealOf(-SWIPE_STEP_PX * 3)).toBe(1);
  });

  it("the cards behind sit lower, smaller and turned, the side alternating", () => {
    const a = edgePose(0);
    const b = edgePose(1);
    expect(a.y).toBeLessThan(b.y);
    expect(a.scale).toBeGreaterThan(b.scale);
    expect(Math.sign(a.rotate)).not.toBe(Math.sign(b.rotate));
  });

  it("the banner names its noun", () => {
    expect(stackBanner(3, "changes")).toBe("3 changes waiting. Swipe or use the arrows to see each one.");
  });

  it("every app's approvals reach the stack through the one queue", () => {
    const q = readFileSync(join(__dirname, "..", "components/ConfirmationQueue.tsx"), "utf-8");
    expect(q).toContain("<CardStack");
  });

  it("the spring stops for a member who asked for less motion", () => {
    const css = readFileSync(join(__dirname, "..", "app/globals.css"), "utf-8");
    const reduce = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(reduce).toContain("--spring-duration: 0.01ms");
  });
});
