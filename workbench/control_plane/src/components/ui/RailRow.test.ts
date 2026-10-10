/**
 * The rail row (owner, 2026-10-10). The markup is read with
 * `renderToStaticMarkup`, as in `InfoTip.test.ts`, because vitest has no
 * DOM. What a browser alone can show, the hover itself, is in
 * `e2e/rail-rows.spec.ts`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RailRow, railRowClass, type RailRowProps } from "./RailRow";

const draw = (props: Partial<RailRowProps> = {}) =>
  renderToStaticMarkup(
    createElement(RailRow, {
      label: "Company Operations",
      meta: 27,
      actions: createElement("button", { type: "button", "aria-label": "Actions" }, "..."),
      ...props,
    }),
  );

/** The `class` of the first element that carries `marker` in its class list. */
function classOf(html: string, marker: string): string {
  const match = html.match(new RegExp(`class="([^"]*\\b${marker}\\b[^"]*)"`));
  return match ? match[1] : "";
}

describe("the trailing zone", () => {
  it("hides the actions at rest with the house reveal", () => {
    const actions = classOf(draw(), "rail-row-actions");
    expect(actions).toContain("reveal-on-hover");
    expect(actions).toContain("rail-row-actions");
  });

  it("shows the meta at rest, and the meta and the actions end the row together", () => {
    const html = draw();
    expect(classOf(html, "rail-row-meta")).toContain("text-muted-foreground");
    const meta = html.indexOf("rail-row-meta");
    const actions = html.indexOf("rail-row-actions");
    expect(meta).toBeGreaterThan(html.indexOf("Company Operations"));
    // The meta closes the label button and the actions follow it at once:
    // the two are one zone at the right end of the row.
    expect(html.slice(meta, actions + "rail-row-actions".length)).toMatch(
      /27<\/span><\/button><span class="[^"]*rail-row-actions$/,
    );
  });

  it("draws no meta and no actions when there are none", () => {
    const html = draw({ meta: null, actions: undefined });
    expect(html).not.toContain("rail-row-meta");
    expect(html).not.toContain("rail-row-actions");
  });

  it("pins the actions open with data-pinned, and only then", () => {
    expect(draw()).not.toContain("data-pinned");
    expect(draw({ pinned: true })).toContain('data-pinned=""');
    // Nothing to hold open: no actions, no pin.
    expect(draw({ pinned: true, actions: undefined })).not.toContain("data-pinned");
  });
});

describe("the label", () => {
  it("is one line, cut with an ellipsis, with no native title", () => {
    const label = classOf(draw(), "truncate");
    expect(label).toContain("min-w-0");
    expect(draw()).not.toMatch(/\stitle="/);
  });

  it("is regular for an item and medium for a group", () => {
    expect(classOf(draw(), "truncate")).not.toContain("font-medium");
    expect(classOf(draw({ tier: "group" }), "truncate")).toContain("font-medium");
  });

  it("is the button's text, so it stays the accessible name", () => {
    expect(draw()).toMatch(/<button type="button"[^>]*>.*Company Operations.*<\/button>/);
  });

  it("gives way to an editor, which takes its place", () => {
    const html = draw({ editor: createElement("input", { "aria-label": "Rename" }) });
    expect(html).toContain('aria-label="Rename"');
    expect(html).not.toContain("<button");
  });
});

describe("the row", () => {
  it("draws one indent step per level, with a guide when asked", () => {
    const html = draw({ depth: 3, guides: true });
    expect(html.match(/w-3 shrink-0 self-stretch/g)).toHaveLength(3);
    expect(html.match(/w-px bg-border/g)).toHaveLength(3);
    expect(draw({ depth: 2 }).match(/w-px bg-border/g)).toBeNull();
  });

  it("marks itself for the e2e and the fence", () => {
    expect(draw()).toContain('data-rail-row=""');
  });

  it("is 32 px on a desktop and 40 px for a finger", () => {
    expect(railRowClass({ selected: false, tier: "item", touch: false })).toContain("min-h-8");
    expect(railRowClass({ selected: false, tier: "item", touch: true })).toContain("min-h-10");
  });

  it("wears the house active pair when selected, and is a group for the reveal", () => {
    const selected = railRowClass({ selected: true, tier: "item", touch: false });
    expect(selected).toContain("bg-primary/10 text-primary");
    expect(selected).toMatch(/^group /);
  });
});

describe("the utilities it relies on", () => {
  const css = readFileSync(fileURLToPath(new URL("../../app/globals.css", import.meta.url)), "utf8");
  const block = (name: string) => {
    const at = css.indexOf(`@utility ${name}`);
    expect(at, `globals.css no longer defines \`${name}\``).toBeGreaterThan(-1);
    return css.slice(at, css.indexOf("\n}", at));
  };

  it("rail-row-actions takes no width and no click at rest, and reveals on hover or focus", () => {
    const body = block("rail-row-actions");
    expect(body).toContain("max-width: 0");
    expect(body).toContain("pointer-events: none");
    expect(body).toContain(".group:hover &");
    expect(body).toContain(".group:has(:focus-visible) &");
    expect(body).toContain(".group[data-pinned] &");
    expect(body, "a media query here hides the actions on a touchscreen laptop").not.toContain("@media");
  });

  it("rail-row-meta gives its place to the actions on the same three states", () => {
    const body = block("rail-row-meta");
    expect(body).toContain(".group:hover &");
    expect(body).toContain(".group:has(:focus-visible) &");
    expect(body).toContain(".group[data-pinned] &");
    expect(body).toContain("display: none");
    expect(body).not.toContain("@media");
  });
});
