/**
 * `Button`'s selected state — the semantics half.
 *
 * Spec: `DESIGN_SYSTEM.md` §3 · H-149.
 *
 * **The defect this closes.** 32 hand-rolled toggles across 21 files,
 * measured 2026-09-21, and no primitive for a control that is ON. Some set
 * `aria-pressed` and styled nothing. Others styled the state and told a
 * screen reader nothing. Two halves of one fact, drifting apart because
 * nothing held them together.
 *
 * `selected` is that one thing. It picks the colour AND answers the screen
 * reader, so they cannot disagree.
 *
 * ⚠️ **A pure function, not a rendered assertion.** `vitest.config.ts` runs
 * in the `node` environment, so a decision living only inside JSX is a
 * decision no test can reach — the same argument `SelectButton.test.ts`
 * makes for its own shape.
 */

import { describe, expect, it } from "vitest";

import { pressedFor } from "./Button";

describe("when a Button says it is pressed", () => {
  it("says nothing at all when it is not a toggle", () => {
    // The overwhelming majority of buttons. `aria-pressed="false"` on an
    // ordinary Save button tells a screen reader it is a toggle that is
    // currently off, which is a lie about the whole control.
    expect(pressedFor({})).toBeUndefined();
  });

  it("reports the state both ways once `selected` is given", () => {
    expect(pressedFor({ selected: true })).toBe(true);
    expect(pressedFor({ selected: false })).toBe(false);
  });

  it("stays silent for a RADIO, even when selected", () => {
    /*
     * The case that made this a function instead of an inline ternary.
     * `SpaceSettings`'s icon chooser is a `radiogroup` of `role="radio"`
     * cells. It wants the selected COLOUR and must not gain
     * `aria-pressed` — a radio that is also a toggle button is two
     * conflicting answers to "what kind of control is this", and a screen
     * reader announces one of them.
     */
    expect(
      pressedFor({ selected: true, role: "radio", ariaChecked: true }),
    ).toBeUndefined();
    expect(pressedFor({ selected: true, role: "tab" })).toBeUndefined();
    expect(pressedFor({ selected: true, ariaChecked: false })).toBeUndefined();
  });

  it("lets an explicit aria-pressed win over everything", () => {
    // A caller that has already thought about it is not overruled — and a
    // call site that predates `selected` keeps working unchanged.
    expect(pressedFor({ ariaPressed: true })).toBe(true);
    expect(pressedFor({ selected: false, ariaPressed: true })).toBe(true);
    expect(
      pressedFor({ selected: true, role: "radio", ariaPressed: "mixed" }),
    ).toBe("mixed");
  });
});
