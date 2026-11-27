/**
 * Task 3559 fence: the touch size of the shared controls.
 *
 * The remove x of an assignee chip is 11 to 13 px. A fingertip needs about 32 px, so the button
 * carries an invisible pseudo-element on a touch screen only. A desktop gets no extra class, so its
 * box does not move. The browser spec `e2e/touch-targets.spec.ts` measures the real boxes. This
 * file fences the source, because the vitest run has no DOM.
 *
 * Mutation 1: delete `${CHIP_REMOVE_TOUCH}` from the remove button. "the remove button carries the
 * touch rule" turns red.
 * Mutation 2: write `before:-inset-3` without the `pointer-coarse:` prefix. "every class is behind
 * pointer-coarse" turns red, because that would grow the desktop hit area.
 * Mutation 3: remove `glass:pointer-coarse:h-10` from one `GLASS_SIZES` entry of `Button.tsx`.
 * "every Button size has a coarse height" turns red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { glassButtonClasses, type ButtonSize, type ButtonVariant } from "@/components/ui/Button";

import { AssigneeChips, CHIP_REMOVE_TOUCH } from "./AssigneeChips";

describe("the assignee chip remove x on a touch screen (task 3559)", () => {
  it("every class is behind pointer-coarse", () => {
    const classes = CHIP_REMOVE_TOUCH.split(/\s+/).filter(Boolean);
    expect(classes.length).toBeGreaterThan(0);
    for (const c of classes) expect(c.startsWith("pointer-coarse:"), c).toBe(true);
  });

  it("grows the tap area by at least 10 px on each side of the x", () => {
    const inset = /pointer-coarse:before:-inset-(\d+(?:\.\d+)?)/.exec(CHIP_REMOVE_TOUCH);
    expect(inset).not.toBeNull();
    // Tailwind step n is n * 0.25 rem. The x is at most 13 px, and 32 px needs 10 px on each side.
    expect(Number(inset![1]) * 4).toBeGreaterThanOrEqual(10);
    expect(CHIP_REMOVE_TOUCH).toContain("pointer-coarse:relative");
    expect(CHIP_REMOVE_TOUCH).toContain("pointer-coarse:before:absolute");
  });

  it("the remove button carries the touch rule", () => {
    const html = renderToStaticMarkup(
      createElement(AssigneeChips, { assignees: ["ann@acme.test"], onRemove: () => undefined }),
    );
    const button = /<button\b[^>]*>/.exec(html)?.[0] ?? "";
    expect(button).toContain('aria-label="Unassign ann@acme.test"');
    expect(button).toContain("pointer-coarse:before:absolute");
  });
});

describe("the shared Button on a touch screen (task 3559)", () => {
  const SIZES: ButtonSize[] = ["sm", "md", "lg", "icon-xs", "icon-sm", "icon"];
  const VARIANTS: ButtonVariant[] = ["primary", "secondary", "ghost", "destructive"];

  it("every Button size has a coarse height", () => {
    for (const size of SIZES) {
      for (const variant of VARIANTS) {
        expect(glassButtonClasses({ variant, size }), `${variant} ${size}`).toContain("glass:pointer-coarse:h-10");
      }
    }
  });

  it("an icon size also has a coarse width", () => {
    for (const size of ["icon-xs", "icon-sm", "icon"] as const) {
      expect(glassButtonClasses({ variant: "ghost", size })).toContain("glass:pointer-coarse:w-10");
    }
  });
});
