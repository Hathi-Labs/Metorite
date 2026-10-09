/**
 * WS-41 I-10b build rule 2 — the one help control.
 *
 * `vitest.config.ts` is `environment: "node"`, so there is no DOM. As in
 * `SelectButton.test.ts`, the behaviour is a pure reducer, and the markup is
 * read with `renderToStaticMarkup`. The slice adds no DOM test dependency.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { shouldDismiss } from "@/lib/outsideClick";

import { INFO_TIP_WIDTH, InfoTip, infoTipFit, infoTipReducer } from "./InfoTip";

const shut = { open: false };
const open = { open: true };

describe("infoTipReducer", () => {
  it("opens and closes on a click or a tap", () => {
    expect(infoTipReducer(shut, { type: "click" })).toEqual(open);
    expect(infoTipReducer(open, { type: "click" })).toEqual(shut);
  });

  it("opens on Enter and on Space", () => {
    expect(infoTipReducer(shut, { type: "key", key: "Enter" })).toEqual(open);
    expect(infoTipReducer(shut, { type: "key", key: " " })).toEqual(open);
  });

  it("closes on Escape, and ignores other keys", () => {
    expect(infoTipReducer(open, { type: "key", key: "Escape" })).toEqual(shut);
    expect(infoTipReducer(shut, { type: "key", key: "Escape" })).toEqual(shut);
    expect(infoTipReducer(open, { type: "key", key: "a" })).toEqual(open);
  });

  it("closes on a press outside, through the shared walker", () => {
    // A node with no parent that is not the surface: really outside.
    const walk = { parent: () => null, isSurface: () => false, isGuarded: () => false };
    expect(infoTipReducer(open, { type: "outside", dismiss: shouldDismiss("x", walk) })).toEqual(shut);
    // A press inside the trigger or the portalled panel does not close it.
    const inside = { ...walk, isGuarded: () => true };
    expect(infoTipReducer(open, { type: "outside", dismiss: shouldDismiss("x", inside) })).toEqual(open);
  });
});

describe("infoTipFit", () => {
  it("hangs from the side with room, and stays inside a 390 px screen", () => {
    const left = infoTipFit({ left: 20, right: 40 }, 390);
    expect(left).toEqual({ align: "start", width: INFO_TIP_WIDTH });
    const right = infoTipFit({ left: 340, right: 360 }, 390);
    expect(right.align).toBe("end");
    // Hung from its right edge at 360, the panel ends at 360 and starts at
    // 360 - width, which is never left of the screen.
    expect(360 - right.width).toBeGreaterThanOrEqual(0);
    const middle = infoTipFit({ left: 180, right: 200 }, 390);
    expect(middle.width).toBeLessThanOrEqual(390 - 180 - 8);
  });
});

describe("InfoTip markup", () => {
  it("is a labelled button with the Info icon, closed by default", () => {
    const html = renderToStaticMarkup(createElement(InfoTip, { label: "Status and stage" }, "help"));
    expect(html).toContain('aria-label="Status and stage"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("cc-control");
    expect(html).toMatch(/lucide-info|lucide/);
    // The text is not in the page until it opens.
    expect(html).not.toContain("help");
  });

  it("says when it is open", () => {
    const html = renderToStaticMarkup(
      createElement(InfoTip, { label: "Status and stage", defaultOpen: true }, "help"),
    );
    expect(html).toContain('aria-expanded="true"');
  });

  it("imports nothing from the substrate (AGENTS.md rule 8)", () => {
    const src = readFileSync(join(__dirname, "InfoTip.tsx"), "utf-8");
    expect(src).not.toMatch(/from\s+["']@base-ui\/react/);
  });
});
