import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusOrderArrows } from "./StatusOrderArrows";

// Task 1792. The Statuses dialog shows its order arrows in plain sight.
// The dialog loads its rows in an effect, so a server render shows only its
// loading state. The arrows are their own component, and this renders it.

function html(index: number, count: number, busy = false): string {
  return renderToStaticMarkup(
    createElement(StatusOrderArrows, {
      name: "In review",
      index,
      count,
      busy,
      onMove: () => {},
    }),
  );
}

/** The opening tag of the button with this aria-label. */
function button(markup: string, label: string): string {
  const tag = markup.match(new RegExp(`<button[^>]*aria-label="${label}"[^>]*>`));
  if (!tag) throw new Error(`no button labelled ${label}`);
  return tag[0];
}

describe("Statuses dialog order arrows", () => {
  it("labels the two buttons with the lane name", () => {
    const markup = html(1, 3);
    expect(markup).toContain('aria-label="Move In review up"');
    expect(markup).toContain('aria-label="Move In review down"');
  });

  it("enables both arrows for a lane in the middle of its stage", () => {
    const markup = html(1, 3);
    expect(button(markup, "Move In review up")).not.toContain('disabled=""');
    expect(button(markup, "Move In review down")).not.toContain('disabled=""');
  });

  it("disables both arrows for a lane alone in its stage", () => {
    const markup = html(0, 1);
    expect(button(markup, "Move In review up")).toContain('disabled=""');
    expect(button(markup, "Move In review down")).toContain('disabled=""');
  });

  it("disables only Up at the top and only Down at the bottom", () => {
    const top = html(0, 2);
    expect(button(top, "Move In review up")).toContain('disabled=""');
    expect(button(top, "Move In review down")).not.toContain('disabled=""');
    const bottom = html(1, 2);
    expect(button(bottom, "Move In review up")).not.toContain('disabled=""');
    expect(button(bottom, "Move In review down")).toContain('disabled=""');
  });

  it("disables both while a write is in flight", () => {
    const markup = html(1, 3, true);
    expect(button(markup, "Move In review up")).toContain('disabled=""');
    expect(button(markup, "Move In review down")).toContain('disabled=""');
  });
});
