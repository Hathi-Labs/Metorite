/**
 * `Input`'s leading icon, and the one class that makes it visible at all.
 *
 * ⚠️ **The defect this pins was invisible for the whole life of the `icon`
 * prop.** The field carries an opaque `bg-background`. An absolutely
 * positioned sibling with no stacking order of its own paints UNDER it, so
 * `icon` reserved its 32px of `pl-8` and then hid the glyph behind the field.
 * Three call sites shipped that way — `FilterBar`, `TriageRail`, `MoveDialog`.
 *
 * Measured 2026-09-18 in the visual rig, at a 4× scale: the magnifier appeared
 * the instant the input's background was set to `transparent`, and not before.
 * It reads as a wrong icon NAME, and it is not one — the `<svg>` is in the DOM
 * at 14×14, `visibility: visible`, `opacity: 1`, stroked slate-400.
 *
 * ⚠️ **What this test can and cannot do, said plainly.**
 * `vitest.config.ts` is `environment: "node"`. There is no DOM here, so no
 * assertion in this suite can see a painted pixel. This is a SOURCE fence, and
 * this tree has twice watched a source fence pass on the value it existed to
 * reject. It catches the realistic regression — somebody tidying away a class
 * that looks decorative — and it cannot catch a `z-10` that is present and
 * ineffective. The proof that the class WORKS is the capture, not this file.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC = fs.readFileSync(
  path.join(__dirname, "Input.tsx"),
  // ⚠️ Windows is the primary dev box and cp1252 is its default. This file
  // carries ⚠️ and — in its prose; reading it without an explicit encoding
  // throws. CLAUDE.md §6 records this.
  "utf-8",
);

/** The `<Icon>` block `Input` renders when `icon` is given. */
const LEADING_ICON = SRC.slice(SRC.indexOf("<Icon"), SRC.indexOf("{field}"));

describe("Input's leading icon", () => {
  it("carries a stacking order, or it is painted under the field", () => {
    expect(LEADING_ICON).toMatch(/\bz-\d+\b/);
  });

  it("is still positioned against the wrapper it sits in", () => {
    // `z-index` does nothing on a statically positioned element, so the two
    // classes are one rule and not two.
    expect(LEADING_ICON).toContain("absolute");
    expect(SRC).toContain('className="relative w-full"');
  });

  it("reserves the room it occupies", () => {
    // The padding and the glyph are a pair. Left alone, the padding indents
    // the text past an icon that is not there — which is the state this file
    // was written to end.
    expect(SRC).toContain('icon ? "pl-8" : ""');
  });
});
