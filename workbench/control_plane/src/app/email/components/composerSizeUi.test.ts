// The parts that draw the size of a composer body (owner request,
// 2026-10-14). The runner has no DOM, so each part is rendered to a string or
// called for its element tree, and the wiring is a scan of the source.
import { readFileSync } from "node:fs";

import { createElement, isValidElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { INITIAL_SIZE, KEY_STEP_PX, type SizeState } from "../lib/composerSize";
import { ComposerBody, ComposerExpandButton, handleGripKey, type ComposerSize } from "./ComposerSize";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

function fakeSize(state: Partial<SizeState> = {}, narrow = false): ComposerSize {
  return {
    state: { ...INITIAL_SIZE, ...state },
    dispatch: vi.fn(),
    persist: vi.fn(),
    paneHeight: 800,
    narrow,
    rootClass: "",
    rootStyle: undefined,
  };
}

function body(size: ComposerSize): string {
  return renderToStaticMarkup(
    createElement(ComposerBody, { size, value: "Hello", children: createElement("textarea", { defaultValue: "Hello" }) }),
  );
}

describe("composer-size: the Expand toggle", () => {
  it("draws Expand, and Collapse once expanded, with an icon of the set", () => {
    const off = renderToStaticMarkup(createElement(ComposerExpandButton, { expanded: false, onToggle: () => {} }));
    expect(off).toContain('aria-label="Expand"');
    expect(off).toContain('data-composer-expand="off"');
    const on = renderToStaticMarkup(createElement(ComposerExpandButton, { expanded: true, onToggle: () => {} }));
    expect(on).toContain('aria-label="Collapse"');
    expect(on).toContain('data-composer-expand="on"');
    // The two glyphs differ, so the state shows without the label.
    expect(on.replace(/aria-label="[^"]*"|data-composer-expand="[^"]*"|title="[^"]*"/g, ""))
      .not.toBe(off.replace(/aria-label="[^"]*"|data-composer-expand="[^"]*"|title="[^"]*"/g, ""));
  });

  it("a click switches the mode", () => {
    const onToggle = vi.fn();
    const el = ComposerExpandButton({ expanded: false, onToggle }) as ReactElement<{ onClick: () => void }>;
    expect(isValidElement(el)).toBe(true);
    el.props.onClick();
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("sits in the header of both composers, beside the close X of the reply", () => {
    const detail = read("./EmailDetail.tsx");
    const header = detail.slice(detail.indexOf("<ComposerExpandButton"), detail.indexOf('<AppIcon name="X" size={14} />'));
    expect(header).toContain('onToggle={() => composerSize.dispatch({ type: "toggleExpand" })}');
    expect(header.length).toBeLessThan(800);
    expect(read("./ConversationView.tsx")).toContain('onToggle={() => size.dispatch({ type: "toggleExpand" })}');
  });
});

describe("composer-size: the grip", () => {
  it("is a focusable horizontal separator with its value and limits", () => {
    const html = body(fakeSize());
    expect(html).toContain('role="separator"');
    expect(html).toContain('aria-orientation="horizontal"');
    expect(html).toContain('tabindex="0"');
    expect(html).toMatch(/aria-valuenow="\d+"/);
    expect(html).toMatch(/aria-valuemin="\d+"/);
    expect(html).toMatch(/aria-valuemax="\d+"/);
    expect(html).toContain("cursor-ns-resize");
  });

  it("is absent on a narrow window and while expanded", () => {
    expect(body(fakeSize({}, true))).not.toContain('role="separator"');
    expect(body(fakeSize({ expanded: true }))).not.toContain('role="separator"');
  });

  it("resizes with the keyboard, keeps the height and holds the key", () => {
    const size = fakeSize();
    const e = { key: "ArrowDown", preventDefault: vi.fn(), stopPropagation: vi.fn() };
    handleGripKey(e, size, 300, 184, 720);
    expect(size.dispatch).toHaveBeenCalledWith({ type: "resize", px: 300 + KEY_STEP_PX });
    expect(size.persist).toHaveBeenCalledWith(300 + KEY_STEP_PX);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(e.stopPropagation).toHaveBeenCalled();

    const up = { key: "ArrowUp", preventDefault: vi.fn(), stopPropagation: vi.fn() };
    handleGripKey(up, size, 300, 184, 720);
    expect(size.dispatch).toHaveBeenLastCalledWith({ type: "resize", px: 300 - KEY_STEP_PX });
  });

  it("lets Escape and every other key pass on", () => {
    const size = fakeSize();
    for (const key of ["Escape", "Enter", "r"]) {
      const e = { key, preventDefault: vi.fn(), stopPropagation: vi.fn() };
      handleGripKey(e, size, 300, 184, 720);
      expect(e.preventDefault).not.toHaveBeenCalled();
      expect(e.stopPropagation).not.toHaveBeenCalled();
    }
    expect(size.dispatch).not.toHaveBeenCalled();
    expect(size.persist).not.toHaveBeenCalled();
  });
});

describe("composer-size: one seam for both composers", () => {
  it("wraps the textarea of the inline reply and of the DraftCard", () => {
    for (const file of ["./EmailDetail.tsx", "./ConversationView.tsx"]) {
      const src = read(file);
      expect(src).toMatch(/useComposerSize\(/);
      expect(src).toContain("data-email-composer=\"\"");
      // The textarea is the child of ComposerBody, and the native grip is off.
      const wrapped = src.slice(src.indexOf("<ComposerBody"), src.indexOf("</ComposerBody>"));
      expect(wrapped).toContain("<textarea");
      expect(wrapped).toContain("resize-none");
      expect(wrapped).not.toContain("resize-y");
    }
  });

  it("keeps the AI panel and the footer outside the body", () => {
    for (const file of ["./EmailDetail.tsx", "./ConversationView.tsx"]) {
      const src = read(file);
      const wrapped = src.slice(src.indexOf("<ComposerBody"), src.indexOf("</ComposerBody>"));
      expect(wrapped).not.toContain("<DraftAssistant");
      expect(wrapped).not.toContain("<Button");
    }
  });

  it("scrolls the thread only, never with scrollIntoView", () => {
    const src = read("./ComposerSize.tsx");
    expect(src).not.toMatch(/\.scrollIntoView\(/);
    expect(src).toContain("revealWithin(");
    // #824: the body passes a wheel on to the thread, which holds it.
    expect(src).not.toMatch(/className=[^>]*overscroll-contain/);
    expect(src).not.toMatch(/overscrollBehavior/);
  });
});
