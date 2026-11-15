/**
 * E7e fence (task 3373): the trigger panel and the palette hint on the phone
 * layout of the workflow editor.
 *
 * The phone toolbar has two rows. The trigger panel opened at `top-12` and
 * covered the second row, so it opens at `top-28` there. The palette of the
 * phone layout has no canvas beside it, so its hint says that a tap adds.
 * Without the prop, each keeps its desktop markup byte for byte.
 *
 * Mutation 1: give the desktop panel `top-28`. "keeps the desktop class" turns red.
 * Mutation 2: drop the `fill` branch of the hint. "says that a tap adds" turns red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import NodePalette from "./NodePalette";
import TriggerPanel from "./TriggerPanel";
import type { Catalog } from "../lib/types";

/** The class of the first element of the markup: the root of the panel. */
const rootClass = (html: string): string => /^<\w+ class="([^"]*)"/.exec(html)?.[1] ?? "";

const panel = (phone?: boolean) =>
  rootClass(
    renderToStaticMarkup(
      createElement(TriggerPanel, {
        triggers: [],
        published: false,
        onChange: () => {},
        onClose: () => {},
        phone,
      }),
    ),
  );

/** The root class of the panel before E7e. A desktop must draw it. */
const DESKTOP_PANEL =
  "absolute right-3 top-12 z-20 w-80 rounded-xl border border-border bg-popover shadow-lg max-h-[80vh] overflow-y-auto";

describe("the trigger panel", () => {
  it("keeps the desktop class, byte for byte", () => {
    expect(panel()).toBe(DESKTOP_PANEL);
    expect(panel(false)).toBe(DESKTOP_PANEL);
  });

  it("opens below the two toolbar rows of the phone layout", () => {
    const words = panel(true).split(/\s+/);
    expect(words).toContain("top-28");
    expect(words).not.toContain("top-12");
    expect(panel(true)).toBe(DESKTOP_PANEL.replace("top-12", "top-28"));
  });
});

const catalog: Catalog = {
  node_types: [],
  condition_ops: ["equals"],
  agents: [],
  integrations: [],
  tools: [],
  modules: [],
};

const palette = (fill?: boolean) =>
  renderToStaticMarkup(createElement(NodePalette, { catalog, onAdd: () => {}, fill }));

describe("the hint of the palette", () => {
  it("keeps the desktop words", () => {
    expect(palette()).toContain("Drag onto the canvas, or click to append");
    expect(palette(false)).toContain("Drag onto the canvas, or click to append");
    expect(palette()).not.toContain("Tap a step to add it");
  });

  it("says that a tap adds on the phone layout", () => {
    expect(palette(true)).toContain("Tap a step to add it");
    expect(palette(true)).not.toContain("Drag onto the canvas");
  });
});
