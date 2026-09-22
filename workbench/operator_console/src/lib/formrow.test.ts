// A row of form fields lines its inputs up, whatever hangs under them.
//
// 🔴 **Owner report, 2026-09-22:** *"the text input sections are horrible UI,
// nothing is properly aligned"*, about the credit-price editor.
//
// Seen in the browser, finally. `.formrow` carried `align-items: flex-end`,
// which aligns the BOTTOM EDGES of its children. "One dollar is ₹" has a
// `.field-hint` under its input and "One credit is ₹" does not, so the taller
// field floated its input UP by the height of that hint while the other kept
// its input at the bottom. Two halves of one control, on two different lines.
//
// ⚠️ This is a CSS fact, so the fence reads the stylesheet. The rendered
// result is proved by the visual rig, which is the only thing that can see it
// — but a rule this easy to "tidy" back needs a test that fails in CI.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
const CSS = readFileSync(join(SRC, "app", "globals.css"), "utf8");

/** The body of the first rule whose selector matches.
 *
 * ⚠️ `selector` is a REGEX fragment, already escaped by its caller — the dot
 * in `.formrow` has to be `\.` or it matches `xformrow` too. Escaping again
 * here turns `\.` into `\\.`, which matches a literal backslash and finds
 * nothing. */
function rule(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const m = re.exec(CSS);
  expect(m, `no rule for ${selector}`).not.toBe(null);
  return m![1];
}

describe(".formrow aligns its inputs", () => {
  it("🔴 never bottom-aligns, which is what split the two inputs", () => {
    const body = rule("\\.formrow");
    expect(
      body,
      "align-items: flex-end aligns the bottom EDGES, so a field carrying a " +
        ".field-hint floats its input up by the height of that hint",
    ).not.toContain("flex-end");
  });

  it("uses a grid with a row for the label, the input and the hint", () => {
    const body = rule("\\.formrow");
    expect(body).toContain("display: grid");
    // Three named rows are what every field shares, so each part lines up
    // with the same part of its neighbour.
    expect(body).toMatch(/grid-template-rows:\s*auto auto auto/);
  });

  it("⚠️ every field spans all three rows through subgrid", () => {
    // Without this each field is its own grid and the alignment is a
    // coincidence of equal label heights, which a two-line label breaks.
    const body = rule("\\.formrow \\.field");
    expect(body).toContain("subgrid");
    expect(body).toMatch(/grid-row:\s*span 3/);
  });

  it("⚠️ degrades to `start`, never back to `flex-end`", () => {
    // A browser without subgrid still has to be readable. `start` misaligns a
    // wrapped label and never misaligns a hint, so it is the right wrong
    // answer. Falling back to the original bug is not.
    expect(CSS).toContain("@supports not (grid-template-rows: subgrid)");
    const body = rule("\\.formrow");
    expect(body).toMatch(/align-items:\s*start/);
  });
});

describe("the price boxes say what unit to type", () => {
  const BOARD = readFileSync(
    join(SRC, "app", "pricing", "PriceBoard.tsx"),
    "utf8",
  );

  it("🔴 names the unit once, for all three boxes", () => {
    // They read "Input, per 1M" — per 1M of WHAT, in WHICH unit. The number
    // is credits, and a rupee figure sits two lines above it.
    expect(BOARD).toContain("credits per 1M tokens");
  });

  it("⚠️ does NOT repeat it in each label, which wrapped to two lines", () => {
    // Measured in the rig: "INPUT — CREDITS PER 1M TOKENS" wraps in a card
    // this wide and looked worse than the gap it closed.
    expect(BOARD).not.toContain('label="Input — credits per 1M tokens"');
  });

  it("⚠️ the unit line spans the whole grid", () => {
    // Left as a grid item it would take one column and shove a box onto the
    // next row.
    expect(rule("\\.legs-unit")).toMatch(/grid-column:\s*1 \/ -1/);
  });
});

describe("pricing says what it actually enforces", () => {
  const FILES = ["app/pricing/PriceBoard.tsx", "app/pricing/CreditPrice.tsx", "lib/help.ts"];

  it("🔴 no surface still asks for an elevated session", () => {
    // D72 turned elevation off and #368 took the control off the surface.
    // Three pricing strings still sent the reader hunting for that button.
    // What the Console enforces is rank: `admin`.
    for (const f of FILES) {
      const src = readFileSync(join(SRC, f), "utf8");
      expect(src, `${f} still promises an elevation window`).not.toContain(
        "elevated admin session",
      );
    }
  });

  it("⚠️ the margin button never uses the card row's word, and says it is optional", () => {
    // 🔴 Two reports, two days. First: the row and the button were both
    // "margin", and the row read "—" while the button read "Change the
    // margin" — a card contradicting its own control. Then: *"what does set
    // the price and set a target margin even have [to do with each other]"*.
    //
    // Nothing. `Set the price` is the only control that changes a bill. This
    // one writes `margin_multiplier` (the Console calls it "an intention")
    // and `margin_floor` (an alarm). Neither is read by `router.py` or
    // `credits.py`. So the label carries "optional" and drops the word the
    // row above it already owns.
    const BOARD = readFileSync(join(SRC, "app", "pricing", "PriceBoard.tsx"), "utf8");
    expect(BOARD).toContain("Profit goal (optional)");
    expect(BOARD).not.toContain('"Set the margin"');
    expect(BOARD).not.toContain('"Set a target margin"');
    // And the editor leads by saying what it does NOT do.
    expect(BOARD).toContain("This changes no bill.");
  });

  it("🔴 the token boxes are explained in the page, not in a tooltip", () => {
    // *"I do not know what cached input is, and what do I need to put in
    // that box."* Every word of the explanation lived in a `title`, which a
    // person must already suspect is there before they can read it.
    const BOARD = readFileSync(join(SRC, "app", "pricing", "PriceBoard.tsx"), "utf8");
    expect(BOARD).toContain("the text a customer sends");
    expect(BOARD).toContain("the text the AI writes back");
    expect(BOARD).toContain("repeated text the vendor already has");
  });

  it("🔴 a blank cached box is named as a REVENUE hole, not a default", () => {
    // `RateCard.cached_input_per_1m` defaults to `Decimal(0)`. Blank does not
    // mean "same as input" — it means the customer pays NOTHING for those
    // tokens, and a re-sent chat history is mostly those tokens.
    const BOARD = readFileSync(join(SRC, "app", "pricing", "PriceBoard.tsx"), "utf8");
    expect(BOARD).toContain("Leave it blank and the customer pays nothing");
    // And it says so again, live, while the form is in that state.
    expect(BOARD).toContain("Cached input is empty");
  });
});
