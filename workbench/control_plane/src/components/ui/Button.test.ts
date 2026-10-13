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

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import Button, { pressedFor } from "./Button";

describe("a Button with href is a link (MD-1b visual review)", () => {
  // A link act must stay a real link (`<a href>`), and it must wear the
  // primitive's classes, so it gets the variant and the house focus ring.
  const html = (props: Record<string, unknown>, label = "Open the day") =>
    renderToStaticMarkup(createElement(Button, props as never, label));

  it("renders an <a href> with the same cc-control classes and the variant", () => {
    const out = html({ href: "/calendar", variant: "secondary", size: "sm", type: "button" });
    expect(out).toMatch(/^<a\b/);
    expect(out).toContain('href="/calendar"');
    expect(out).toContain("cc-control");
    expect(out).toContain("border border-border");
    // Button-only attributes and the toggle semantics never reach a link.
    expect(out).not.toContain('type="button"');
    expect(out).not.toContain("aria-pressed");
    expect(out).not.toContain("<button");
  });

  it("draws the same classes as the button form, for each variant", () => {
    const klass = (s: string) => /class="([^"]*)"/.exec(s)?.[1];
    for (const variant of ["primary", "secondary", "ghost"]) {
      expect(klass(html({ href: "/x", variant })), variant).toBe(klass(html({ variant })));
    }
  });

  it("draws a disabled or loading link as the disabled button, because it has nowhere to go", () => {
    expect(html({ href: "/x", disabled: true })).toMatch(/^<button\b[^>]*disabled=""/);
    expect(html({ href: "/x", loading: true })).toMatch(/^<button\b/);
  });
});

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
