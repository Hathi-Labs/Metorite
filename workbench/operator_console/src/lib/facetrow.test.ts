// The filter hint takes a row of its own.
//
// 🔴 **Owner report, 2026-09-24, with a screenshot.** On the Models page the
// hint sentence rendered BESIDE the last filter chip, so "Needs attention"
// and a full sentence shared a line and the row read as broken.
//
// It is a flex sibling of the chips inside `.facetrow`, which wraps — so the
// sentence landed wherever the chips happened to stop. That position is a
// function of how many facets the data produces, which is why it looked fine
// in some states and wrong in others.
//
// Verified in the browser before and after, with `e2e/visual`.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
const CSS = readFileSync(join(SRC, "app", "globals.css"), "utf8");

/** The body of one rule, found by its exact selector text.
 *
 * ⚠️ String search, not a regex. A regex built from a CSS selector needs its
 * dots escaped, and getting that wrong yields a pattern that quietly matches
 * nothing — which reads as "the rule is missing" and sends the next reader
 * hunting a stylesheet bug that is not there. It cost me two rounds here.
 */
function body(selector: string): string {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `no rule for ${selector}`).toBeGreaterThan(-1);
  const open = CSS.indexOf("{", at);
  return CSS.slice(open + 1, CSS.indexOf("}", open));
}

describe(".facetrow puts its hint on its own line", () => {
  it("🔴 the hint spans the whole row, so it never shares a line with a chip", () => {
    // `flex-basis: 100%` starts a new line whatever the chips did — at every
    // width and for any number of facets, which a margin could not.
    expect(body(".facetrow > .muted.small")).toContain("flex-basis: 100%");
  });

  it("⚠️ the row still WRAPS, or the chips would overflow instead", () => {
    // The fix relies on wrapping. Turning it off would push the chips out of
    // the container rather than onto a second line.
    expect(body(".facetrow")).toContain("flex-wrap: wrap");
  });

  it("⚠️ it does NOT reorder — the markup is already last", () => {
    // An explicit `order` would make the visual sequence depend on a number
    // the reading order does not, and a screen reader would then disagree
    // with the screen.
    expect(body(".facetrow > .muted.small")).not.toContain("order:");
  });
});
