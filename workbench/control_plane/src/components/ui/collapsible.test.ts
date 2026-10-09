/**
 * A focused field inside a folding section keeps its whole focus ring
 * (owner report, 2026-10-09: the task's Description ring was cut off at both
 * ends).
 *
 * The panel clips, and the ring is drawn outside the field. So the panel must
 * leave at least the ring's reach (its width plus its offset) on the sides
 * and the bottom, at the SMALLEST density, where a Tailwind unit is smallest.
 * Both numbers are read from where they live, so a thicker ring or a denser
 * density fails here instead of in a member's eyes.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { DENSITY_SCALE } from "@/lib/theme/types";

import { PANEL_CLASS } from "./Collapsible";

const css = readFileSync(fileURLToPath(new URL("../../app/globals.css", import.meta.url)), "utf8");

const px = (re: RegExp) => Number(re.exec(css)?.[1]);
/** Tailwind's spacing unit is 0.25rem; the root scales with density. */
const unitsOf = (cls: string) => Number(new RegExp(`(?:^|\\s)${cls}-(\\d+(?:\\.\\d+)?)(?:\\s|$)`).exec(PANEL_CLASS)?.[1]);

describe("the folding panel leaves room for a focus ring", () => {
  const ring = px(/--control-focus-ring:\s*(\d+(?:\.\d+)?)px/);
  const offset = px(/\.cc-control:focus-visible[^}]*outline-offset:\s*(\d+(?:\.\d+)?)px/);
  const smallest = Math.min(...Object.values(DENSITY_SCALE).map(Number));

  it("reads the ring and the densities it guards", () => {
    expect(ring).toBeGreaterThan(0);
    expect(offset).toBeGreaterThanOrEqual(0);
    expect(smallest).toBeGreaterThan(0);
  });

  for (const side of ["px", "pb"] as const) {
    it(`${side} reaches past the ring at the smallest density`, () => {
      const room = unitsOf(side) * 4 * smallest;
      expect(room, `${PANEL_CLASS}: ${room}px of room, the ring needs ${ring + offset}px`).toBeGreaterThanOrEqual(ring + offset);
    });
  }

  it("cancels its own padding, so the content does not move", () => {
    expect(unitsOf("-mx")).toBe(unitsOf("px"));
    expect(unitsOf("-mb")).toBe(unitsOf("pb"));
  });
});
