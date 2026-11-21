/**
 * The Undo chips of the decisions that a chat keeps (HRD-12b). `DecisionChipsView`
 * holds no hook, so the test calls it and walks the element tree. No DOM.
 *
 * Mutations (R7): draw a chip with no items, and the empty case goes red. Call
 * `onUndo` with the text and not the id, and the id case goes red.
 */
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { COLLAPSED_LIMIT, DecisionChipsView, focusTargetAfterUndo } from "./DecisionChips";

type El = ReactElement<Record<string, unknown>>;

function walk(node: ReactNode, out: El[] = []): El[] {
  if (Array.isArray(node)) node.forEach((n) => walk(n, out));
  else if (isValidElement(node)) {
    const el = node as El;
    out.push(el);
    walk(el.props.children as ReactNode, out);
  }
  return out;
}

const ITEMS = [
  { id: "0123456789ab", kind: "decision", text: "TEDx is done." },
  { id: "ba9876543210", kind: "decision", text: "We use the Pune vendor." },
];

describe("DecisionChipsView", () => {
  it("draws nothing when no decision is kept", () => {
    expect(DecisionChipsView({ items: [], busyId: null, failedId: null, onUndo: vi.fn() })).toBeNull();
  });

  it("draws one chip for each decision, with its text and an Undo", () => {
    const html = renderToStaticMarkup(
      createElement(DecisionChipsView, { items: ITEMS, busyId: null, failedId: null, onUndo: vi.fn() }),
    );
    expect(html).toContain("Kept for this chat");
    expect(html).toContain("TEDx is done.");
    expect(html).toContain("We use the Pune vendor.");
    expect(html.match(/Undo<\/button>/g)).toHaveLength(2);
    expect(html).toContain('aria-label="Undo: TEDx is done."');
  });

  it("calls onUndo with the id of that chip", () => {
    const onUndo = vi.fn();
    const tree = DecisionChipsView({ items: ITEMS, busyId: null, failedId: null, onUndo });
    const buttons = walk(tree).filter((e) => typeof e.props.onClick === "function");
    expect(buttons).toHaveLength(2);
    (buttons[1].props.onClick as () => void)();
    expect(onUndo).toHaveBeenCalledWith("ba9876543210");
  });

  it("disables every Undo while one is in flight, and says when an Undo failed", () => {
    const html = renderToStaticMarkup(
      createElement(DecisionChipsView, { items: ITEMS, busyId: ITEMS[0].id, failedId: ITEMS[1].id, onUndo: vi.fn() }),
    );
    expect(html.match(/disabled=""/g)).toHaveLength(2);
    expect(html).toContain("Could not undo. Try again.");
    expect(html.indexOf("Could not undo")).toBeGreaterThan(html.indexOf("Pune vendor"));
  });

  it("offers no Undo while a reply streams", () => {
    const html = renderToStaticMarkup(
      createElement(DecisionChipsView, { items: ITEMS, busyId: null, failedId: null, onUndo: vi.fn(), locked: true }),
    );
    expect(html.match(/disabled=""/g)).toHaveLength(2);
  });
});

const MANY = Array.from({ length: 10 }, (_, i) => ({ id: `id${i}`, kind: "decision", text: `Decision ${i}` }));
const base = { busyId: null, failedId: null, onUndo: vi.fn(), onToggle: vi.fn() };

describe("DecisionChipsView collapse", () => {
  it("draws the first three chips and a Show 7 more toggle", () => {
    const html = renderToStaticMarkup(createElement(DecisionChipsView, { ...base, items: MANY }));
    expect(COLLAPSED_LIMIT).toBe(3);
    expect(html.match(/Undo<\/button>/g)).toHaveLength(3);
    expect(html).toContain("Show 7 more");
    expect(html).not.toContain("Decision 3");
  });

  it("draws every chip and Show fewer when expanded", () => {
    const html = renderToStaticMarkup(createElement(DecisionChipsView, { ...base, items: MANY, expanded: true }));
    expect(html.match(/Undo<\/button>/g)).toHaveLength(10);
    expect(html).toContain("Show fewer");
  });

  it("draws no toggle for three chips or fewer", () => {
    const html = renderToStaticMarkup(createElement(DecisionChipsView, { ...base, items: MANY.slice(0, 3) }));
    expect(html).not.toContain("Show ");
  });
});

describe("focusTargetAfterUndo", () => {
  const ids = ["a", "b", "c"];
  it("goes to the next chip", () => {
    expect(focusTargetAfterUndo(ids, "b", true)).toEqual({ kind: "undo", id: "c" });
  });
  it("goes to the previous chip when the last one went", () => {
    expect(focusTargetAfterUndo(ids, "c", true)).toEqual({ kind: "undo", id: "b" });
  });
  it("goes to the lead text when none is left", () => {
    expect(focusTargetAfterUndo(["a"], "a", true)).toEqual({ kind: "lead" });
  });
  it("stays on the same chip when the Undo failed", () => {
    expect(focusTargetAfterUndo(ids, "b", false)).toEqual({ kind: "undo", id: "b" });
  });
});
