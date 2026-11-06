/**
 * The frame of a record card (ORC-1, D181, `specs/one_record_card.md` §6).
 *
 * The runner has no DOM, so this reads the classes of each stop, the Escape
 * rule as a pure function, and the markup from `renderToStaticMarkup`.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import CardPanel, { cardEscape, cardFrameClasses, PAGE_MIN_REM, sideCardFits } from "@/components/CardPanel";
import { LAYER_CLASS } from "@/lib/layers";
import { PANEL_WIDTH_CLASS } from "@/lib/panelMode";

const escape = { key: "Escape" };
const body = { tagName: "BODY" };

function draw(stop: "side" | "full" | "phone"): string {
  return renderToStaticMarkup(
    createElement(CardPanel, {
      stop,
      label: "Deal: Acme",
      onClose: () => undefined,
      children: "the body",
    }),
  );
}

describe("the three stops of the frame", () => {
  it("the side stop is the row's own column, with no scrim and no layer", () => {
    const side = cardFrameClasses("side");
    expect(side.host).toBe("contents");
    expect(side.card).toBe("contents");
    expect(side.aside).toContain(PANEL_WIDTH_CLASS.side);
    expect(side.aside).toContain("border-l");
    expect(side.aside).toContain("shrink-0");
  });

  it("the full stop is centered over the page column, under the AI pill", () => {
    const full = cardFrameClasses("full");
    expect(full.host).toContain("justify-center");
    expect(full.host).toContain("bg-background/70");
    expect(full.host).toContain(LAYER_CLASS.taskCard);
    // Inside the page column, so the shell bar and the assistant stay in view.
    expect(full.host).toMatch(/\babsolute\b/);
    expect(full.host).not.toMatch(/\bfixed\b/);
    expect(full.card).toContain(PANEL_WIDTH_CLASS.full);
    expect(full.card).toContain("rounded-lg");
  });

  it("the phone stop fills the screen above the nav, with no cap on its width", () => {
    const phone = cardFrameClasses("phone");
    expect(phone.host).toContain("task-card-phone");
    expect(phone.host).toContain(LAYER_CLASS.taskCard);
    expect(phone.aside).not.toContain("max-w");
    expect(phone.card).not.toContain("max-w");
  });

  it("each stop draws the same tree, so a change of stop keeps the card's state", () => {
    const shape = (html: string) => (html.match(/<\/?[a-z]+/g) ?? []).join(" ");
    expect(shape(draw("full"))).toBe(shape(draw("side")));
    expect(shape(draw("phone"))).toBe(shape(draw("side")));
  });

  it("the aside carries the card's name and can take the focus", () => {
    const html = draw("side");
    expect(html).toContain('aria-label="Deal: Acme"');
    expect(html).toContain('tabindex="-1"');
    expect(html).toContain("the body");
  });
});

describe("a docked card leaves the page usable", () => {
  const rem = 16;
  it("docks at 1440, with the rail of views open", () => {
    expect(sideCardFits(1160, 224, rem)).toBe(true);
  });

  it("docks at 1280", () => {
    expect(sideCardFits(1000, 224, rem)).toBe(true);
  });

  it("stands over the page with the assistant docked at 1440, which left the board 152 px", () => {
    expect(sideCardFits(760, 224, rem)).toBe(false);
  });

  it("the edge is the card and the page minimum", () => {
    const edge = 24 * rem + PAGE_MIN_REM * rem;
    expect(sideCardFits(edge, 0, rem)).toBe(true);
    expect(sideCardFits(edge - 1, 0, rem)).toBe(false);
  });

  it("follows the density, because each width is in rem", () => {
    const edge = 24 * 14 + PAGE_MIN_REM * 14;
    expect(sideCardFits(edge, 0, 14)).toBe(true);
    expect(sideCardFits(edge, 0, 16)).toBe(false);
  });

  it("an unmeasured row says yes", () => {
    expect(sideCardFits(0, 0, rem)).toBe(true);
  });
});

describe("what Escape means for a card", () => {
  it("closes from inside the card or from the page that holds it", () => {
    expect(cardEscape(escape, body, true, false)).toBe("close");
  });

  it("leaves a key from outside the page alone, such as the chat composer", () => {
    expect(cardEscape(escape, { tagName: "TEXTAREA", value: "" }, false, false)).toBeNull();
  });

  it("leaves the key alone while a dialog is open over the card", () => {
    expect(cardEscape(escape, body, true, true)).toBeNull();
  });

  it("leaves the key to a popup that holds the focus", () => {
    const target = {
      tagName: "BUTTON",
      closest: (selector: string) => (selector.includes("aria-expanded") ? {} : null),
    };
    expect(cardEscape(escape, target, true, false)).toBeNull();
  });

  it("leaves a key that another handler already took", () => {
    expect(cardEscape({ ...escape, defaultPrevented: true }, body, true, false)).toBeNull();
  });

  it("blurs a field with text first, so a half-typed note survives", () => {
    expect(cardEscape(escape, { tagName: "TEXTAREA", value: "call back" }, true, false)).toBe("blur");
    expect(cardEscape(escape, { tagName: "TEXTAREA", value: " " }, true, false)).toBe("close");
  });

  it("ignores another key and a key with a modifier", () => {
    expect(cardEscape({ key: "j" }, body, true, false)).toBeNull();
    expect(cardEscape({ ...escape, altKey: true }, body, true, false)).toBeNull();
  });
});
