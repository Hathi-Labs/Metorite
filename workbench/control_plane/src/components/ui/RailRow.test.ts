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

import { RailRow, expandKey, railRowClass, togglePress, type RailRowProps } from "./RailRow";

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

  it("flags a row with actions, so only that row swaps its count", () => {
    expect(draw()).toContain('data-has-actions=""');
    expect(draw({ actions: undefined })).not.toContain("data-has-actions");
  });

  it("adds no second accessible name to the label", () => {
    const src = readFileSync(fileURLToPath(new URL("./RailRow.tsx", import.meta.url)), "utf8");
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toContain("aria-describedby");
    expect(code).not.toMatch(/\stitle=/);
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

describe("the chevron takes the icon's slot", () => {
  const icon = createElement("svg", { "aria-label": "Space — Company Operations" });
  const open = (expanded: boolean) =>
    draw({ icon, expand: { expanded, onToggle: () => {} } });

  it("is a labelled toggle at rest, with aria-expanded, so a keyboard reaches it", () => {
    const html = open(true);
    expect(html).toMatch(/<button[^>]*aria-label="Collapse Company Operations"[^>]*>/);
    expect(html).toMatch(/aria-expanded="true"/);
    expect(open(false)).toMatch(/aria-label="Expand Company Operations"/);
    expect(open(false)).toMatch(/aria-expanded="false"/);
  });

  it("is transparent at rest through rail-row-chevron, and the icon gives way on hover", () => {
    const html = open(true);
    expect(classOf(html, "rail-row-chevron")).toContain("absolute");
    // The icon stays inside the label button, so its words stay in the row's name.
    expect(html).toMatch(/<button type="button"[^>]*><span data-rail-icon="" class="[^"]*rail-row-icon/);
  });

  it("adds no chevron column: the toggle hangs from a zero-width anchor", () => {
    expect(open(true)).toMatch(/<span class="relative w-0 shrink-0 self-stretch"><button/);
    expect(open(true)).not.toContain("w-4.5");
  });

  it("draws no toggle on a leaf, and none over an editor", () => {
    expect(draw({ icon })).not.toContain("data-rail-toggle");
    expect(draw({ icon })).not.toContain("rail-row-icon");
    const editing = draw({
      icon,
      expand: { expanded: true, onToggle: () => {} },
      editor: createElement("input", { "aria-label": "Rename" }),
    });
    expect(editing).not.toContain("data-rail-toggle");
  });

  it("a touch press on a row that is not selected selects it, and every other press switches it", () => {
    expect(togglePress("touch", false, false)).toBe("select");
    expect(togglePress("touch", true, false)).toBe("toggle");
    expect(togglePress("mouse", false, false)).toBe("toggle");
    expect(togglePress("pen", false, false)).toBe("toggle");
    // Enter and Space carry no pointer.
    expect(togglePress(null, false, false)).toBe("toggle");
    // A phone shows the chevron, so the finger sees what it taps.
    expect(togglePress("touch", false, true)).toBe("toggle");
    // A click with detail 0 is Enter or Space. A touch press left behind by
    // a pan or a long-press must not turn the key into a select.
    expect(togglePress("touch", false, false, true)).toBe("toggle");
  });

  it("opens on ArrowRight and closes on ArrowLeft, and leaves other keys alone", () => {
    expect(expandKey("ArrowRight", false)).toBe(true);
    expect(expandKey("ArrowRight", true)).toBe(false);
    expect(expandKey("ArrowLeft", true)).toBe(true);
    expect(expandKey("ArrowLeft", false)).toBe(false);
    expect(expandKey("Enter", false)).toBe(false);
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

  it("rail-row-chevron hides at rest and shows on hover or focus, and rail-row-icon gives way", () => {
    const chevron = block("rail-row-chevron");
    expect(chevron).toContain("opacity: 0");
    // A transparent toggle must not catch the tap meant for the row's icon.
    const [restRule, shownRule] = chevron.split("{").slice(1);
    expect(restRule).toContain("pointer-events: none");
    expect(shownRule).toContain("pointer-events: auto");
    expect(chevron).toContain(".group[data-pinned] &");
    expect(chevron).toContain(".group:hover &");
    expect(chevron).toContain(".group:has(:focus-visible) &");
    expect(chevron).not.toContain("@media");
    const iconBody = block("rail-row-icon");
    expect(iconBody).toContain(".group:hover &");
    expect(iconBody).toContain("opacity: 0");
    expect(iconBody).not.toContain("@media");
  });

  it("rail-row-meta gives way only on a row with actions, and only from sight", () => {
    const body = block("rail-row-meta");
    // A row with no actions keeps its count: nothing would take its place.
    expect(body).toContain(".group[data-has-actions]:hover &");
    expect(body).toContain(".group[data-has-actions]:has(:focus-visible) &");
    expect(body).toContain(".group[data-has-actions][data-pinned] &");
    expect(body).not.toMatch(/\.group:hover &/);
    // `display: none` took the count out of the accessible name on focus.
    expect(body).not.toContain("display: none");
    expect(body).toContain("clip-path: inset(50%)");
    expect(body).not.toContain("@media");
  });
});
