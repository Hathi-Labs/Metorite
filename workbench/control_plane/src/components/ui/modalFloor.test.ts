/**
 * STU-D23a-2 fence: a dialog in the floor view of a Studio app takes the touch size.
 *
 * `Modal` draws into a portal, outside the root of the floor view. Inside the floor view
 * (`FloorContext`, `lib/floorContext.ts`) its popup takes the same `data-floor` attribute as that
 * root, so each field and button in it takes its touch size. Outside the floor view, nothing in
 * the markup changes. A server render cannot draw a portal, so the substrate is a stand-in here:
 * each part draws one `div` with its props, and the test reads the props that `Modal` gives.
 *
 * Mutation 1: set `data-floor` on the popup always. "has no data-floor outside the floor view"
 * turns red.
 * Mutation 2: drop the read of the context (`useFloor`). "puts data-floor on the popup, once"
 * turns red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@base-ui/react/dialog", async () => {
  const react = await import("react");
  type Props = Record<string, unknown> & { children?: unknown };
  const pass = (props: Props) => react.createElement(react.Fragment, null, props.children as never);
  // One `div` for each part, with the props that `Modal` passes. A function prop draws nothing.
  const part = (name: string) => (props: Props) => {
    const attrs: Record<string, unknown> = { "data-part": name };
    for (const [key, value] of Object.entries(props)) {
      if (key !== "children" && value !== undefined && typeof value !== "function") attrs[key] = value;
    }
    return react.createElement("div", attrs, props.children as never);
  };
  return {
    Dialog: {
      Root: pass, Portal: pass, Backdrop: part("backdrop"), Viewport: part("viewport"),
      Popup: part("popup"), Title: part("title"), Description: part("description"),
    },
  };
});

import Modal from "./Modal";
import { FloorContext } from "@/lib/floorContext";

/** A dialog with a title and one child. `floor` undefined: no provider at all. */
function draw(floor?: boolean): string {
  const dialog = createElement(Modal, { open: true, onClose: () => {}, title: "New job" },
    createElement("i", { "data-child": "" }));
  return renderToStaticMarkup(floor === undefined ? dialog : createElement(FloorContext.Provider, { value: floor }, dialog));
}

describe("a Modal outside the floor view", () => {
  it("has no data-floor outside the floor view", () => {
    expect(draw()).not.toContain("data-floor");
    expect(draw(false)).not.toContain("data-floor");
  });

  it("draws the same markup with no provider and with the provider off", () => {
    expect(draw(false)).toBe(draw());
  });
});

describe("a Modal inside the floor view", () => {
  it("puts data-floor on the popup, once", () => {
    const html = draw(true);
    expect(html.match(/data-floor=""/g)).toHaveLength(1);
    expect(html).toMatch(/<div data-part="popup"[^>]* data-floor=""/);
    expect(html).not.toMatch(/<div data-part="(backdrop|viewport)"[^>]* data-floor/);
  });

  it("holds the title, the close button and the child inside that popup", () => {
    const html = draw(true);
    const popup = html.slice(html.indexOf('data-part="popup"'));
    expect(popup).toContain("New job");
    expect(popup).toContain('aria-label="Close"');
    expect(popup).toContain("data-child");
  });

  it("changes nothing else: without the attribute, the markup is the usual one", () => {
    expect(draw(true).replace(' data-floor=""', "")).toBe(draw());
  });
});
