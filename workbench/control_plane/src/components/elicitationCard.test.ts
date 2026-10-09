/**
 * The ask_questions card (owner report, 2026-10-09).
 *
 * Mutations caught: the recommended mark goes back to a bare star; an option
 * goes back to a hand-made selected fill instead of `Button`'s `selected`
 * (the old `bg-primary/20 text-primary-foreground` put light ink on a pale
 * tint). The 200 and 409 click paths run in a real browser in
 * `e2e/genui-option-picker.spec.ts`.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ElicitationCard from "./ElicitationCard";

const html = renderToStaticMarkup(createElement(ElicitationCard, {
  questions: [{
    header: "Forward",
    question: "How should we forward it?",
    allowFreeformInput: false,
    options: [
      { label: "Forward with the PDF", recommended: true },
      { label: "Send a link" },
    ],
  }],
  onSubmit: () => {},
}));

describe("ElicitationCard", () => {
  it("draws the word Recommended on the recommended option, and no star", () => {
    expect(html).toContain(">Recommended<");
    expect(html).not.toMatch(/[★⭐]/);
  });

  it("draws each option as the shared Button toggle", () => {
    expect(html.match(/aria-pressed="false"/g)?.length).toBe(2);
    expect(html).not.toContain("text-primary-foreground\">Forward");
    expect(html).not.toContain("bg-primary/20");
  });
});
