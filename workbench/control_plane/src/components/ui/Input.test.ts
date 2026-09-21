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

import { splitWidth } from "./Input";

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
    // ⚠️ Was `className="relative w-full"` until 2026-09-20. The wrapper now
    // takes the caller's width instead of always filling the row — see the
    // `splitWidth` suite below for why. `relative` is the half this test
    // cares about, so it is the half it asserts.
    expect(SRC).toContain("relative ${width}");
  });

  it("reserves the room it occupies", () => {
    // The padding and the glyph are a pair. Left alone, the padding indents
    // the text past an icon that is not there — which is the state this file
    // was written to end.
    expect(SRC).toContain('icon ? "pl-8" : ""');
  });
});

/**
 * The width a caller asks for is the width they get.
 *
 * ## The defect
 *
 * `BASE` carried `w-full`, and the class string was built as
 * `` `${BASE} ${className}` ``. That READS as "the caller wins" and is not
 * how Tailwind works: precedence comes from the order rules appear in the
 * generated STYLESHEET, never from their order in the `class` attribute.
 * `w-full` and `w-40` have equal specificity, and `w-full` sorts later.
 *
 * So ELEVEN call sites were passing a width that did nothing. The one that
 * made it visible was the Projects bulk bar: four fields written `w-40` and
 * `w-32` on a `flex-wrap` row, each rendering full width, so every field
 * took a line of its own and pushed the board a third of a screen down. It
 * reads as a missing layout rule. The layout rule was there.
 *
 * ⚠️ Unlike the suite above, these are REAL unit tests over an exported
 * function rather than a source scan. `splitWidth` is exported for that
 * reason.
 */
describe("splitWidth", () => {
  it("defaults to w-full when the caller says nothing", () => {
    // Every existing call site leans on this, so the default cannot move.
    expect(splitWidth("")).toEqual({ width: "w-full", rest: "" });
  });

  it("lets a fixed width replace the default", () => {
    // THE case from the bulk bar.
    expect(splitWidth("w-40")).toEqual({ width: "w-40", rest: "" });
  });

  it("keeps non-width classes off the wrapper", () => {
    // `text-right` styles the text, not the box. Sending it to the wrapper
    // would silently drop it.
    expect(splitWidth("w-32 text-right font-mono")).toEqual({
      width: "w-32",
      rest: "text-right font-mono",
    });
  });

  it("treats the flex-basis idiom as a width", () => {
    // `min-w-0 flex-1 basis-32` is how two call sites size a field that must
    // shrink. Adding `w-full` to that stops it shrinking.
    expect(splitWidth("min-w-0 flex-1 basis-32").width).toBe(
      "min-w-0 flex-1 basis-32",
    );
  });

  it("carries max-w and min-w too", () => {
    expect(splitWidth("max-w-xs").width).toBe("max-w-xs");
    expect(splitWidth("min-w-40").width).toBe("min-w-40");
  });

  it("does not mistake a class that merely STARTS like a width", () => {
    // ⚠️ `whitespace-nowrap` begins with `w`, and `flex-wrap` with `flex-`.
    // A looser regex would move text styling onto the wrapper and lose it,
    // which is a worse bug than the one being fixed because it is silent.
    const got = splitWidth("whitespace-nowrap flex-wrap font-medium");
    expect(got.width).toBe("w-full");
    expect(got.rest).toBe("whitespace-nowrap flex-wrap font-medium");
  });

  it("survives ragged spacing", () => {
    expect(splitWidth("  w-20   text-xs  ")).toEqual({
      width: "w-20",
      rest: "text-xs",
    });
  });
});

/**
 * Every field primitive applies the default width.
 *
 * ⚠️ **This suite exists because the fix above was applied to ONE of three
 * primitives and nobody noticed for three days.**
 *
 * `w-full` left `BASE` on 2026-09-18. `Input` grew `splitWidth` in the same
 * commit. `Textarea` and `Select` did not, and both fall back to an intrinsic
 * width when no class gives them one: a `<textarea>` to its `cols` attribute,
 * which defaults to 20 characters, and a `<select>` to its longest option.
 *
 * The visible result was Projects' comment box — 206px wide in a 447px panel,
 * sitting in the corner under a full-width Comment button. Owner-reported
 * 2026-09-21, from a screenshot.
 *
 * A per-component test would not have caught this, because the components
 * that were wrong had no test. This one asserts the RULE across all three, so
 * a fourth primitive that forgets it fails here.
 */
describe("the default-width rule reaches every primitive", () => {
  /** One exported component's body, from its `export function` to the next. */
  function bodyOf(name: string): string {
    const from = SRC.indexOf(`export function ${name}(`);
    expect(from, `${name} is exported from Input.tsx`).toBeGreaterThan(-1);
    const after = SRC.indexOf("\nexport ", from + 1);
    return SRC.slice(from, after === -1 ? SRC.length : after);
  }

  it.each(["Input", "Textarea", "Select"])(
    "%s asks splitWidth what width to draw",
    (name) => {
      expect(bodyOf(name)).toContain("splitWidth(className)");
    },
  );

  it.each(["Input", "Textarea", "Select"])(
    "%s spends the width it was given",
    (name) => {
      // Calling `splitWidth` and then dropping `width` is the same bug with
      // an extra step, and it type-checks.
      expect(bodyOf(name)).toMatch(/\$\{width\}/);
    },
  );

  it("no primitive puts w-full back into the shared BASE", () => {
    // Where this started. `BASE` is concatenated BEFORE the caller's classes
    // and Tailwind decides precedence by stylesheet order, so a `w-full`
    // here silently beats every caller's `w-40` again.
    // The LITERAL only. The prose between it and `WIDTH_RE` explains the
    // defect and says `w-full` five times over, so a looser slice fails on
    // the documentation instead of on the code.
    const from = SRC.indexOf("const BASE =");
    const base = SRC.slice(from, SRC.indexOf(";", from));
    expect(base).not.toMatch(/\bw-full\b/);
  });
});
