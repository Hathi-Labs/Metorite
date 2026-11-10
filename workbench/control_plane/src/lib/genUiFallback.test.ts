/**
 * An empty generative-UI node draws nothing, and an unknown one draws its
 * words, never "unsupported UI element". `lib/genUiFallback.ts`.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import GenerativeUINode from "@/components/GenerativeUINode";
import { renderTemplate } from "@/components/genUITemplates";
import { fallbackLines, isEmptyNode, treeItems } from "@/lib/genUiFallback";

const draw = (spec: unknown) => renderToStaticMarkup(createElement(GenerativeUINode, { spec }));

const TREE = {
  type: "tree",
  props: {
    title: "Firmware",
    items: [
      { label: "Bootloader", children: [{ label: "Flash layout" }, { title: "Secure boot", children: [{ name: "Key ceremony" }] }] },
      { label: "Release" },
    ],
  },
};

describe("isEmptyNode", () => {
  it.each([
    { type: "text", props: { text: "" } },
    { type: "markdown", props: { text: "   " } },
    { type: "heading" },
    { type: "badge", props: {} },
    { type: "callout", props: {} },
    { type: "card", children: [{ type: "text", props: { text: "" } }] },
    { type: "stack", children: [] },
    { type: "row", children: [{ type: "markdown", props: { text: "" } }] },
    { type: "list", props: { items: ["", null] } },
    { type: "keyValue", props: { pairs: [] } },
    { type: "table", props: { rows: [], columns: [""] } },
    { type: "template", props: {} },
    { type: "html", props: { code: "" } },
    { type: "gauge", props: {} },
    null,
    "text",
  ])("%j is empty", (node) => {
    expect(isEmptyNode(node)).toBe(true);
  });

  it.each([
    { type: "text", props: { text: "hi" } },
    { type: "divider" },
    { type: "card", props: { title: "Late tasks" } },
    { type: "callout", props: { text: "Heads up" } },
    { type: "stack", children: [{ type: "divider" }] },
    { type: "table", props: { rows: [["a"]] } },
    // Review round 1: a "no results" table with `{header}` columns.
    { type: "table", props: { columns: [{ key: "title", header: "Task" }], rows: [] } },
    { type: "template", props: { name: "planCard" } },
    { type: "gauge", props: { title: "Velocity" } },
  ])("%j is not empty", (node) => {
    expect(isEmptyNode(node)).toBe(false);
  });
});

describe("an empty node renders nothing", () => {
  it("an empty callout draws no bar", () => {
    expect(draw({ type: "callout", props: {} })).toBe("");
  });

  it("a stack of empty texts draws no gaps", () => {
    expect(draw({ type: "stack", children: [{ type: "text", props: { text: "" } }, { type: "markdown", props: { text: " " } }] })).toBe("");
  });

  it("an empty child inside a card draws nothing, and the card stays", () => {
    const html = draw({ type: "card", props: { title: "Late" }, children: [{ type: "callout", props: {} }] });
    expect(html).toContain("Late");
    expect(html).not.toContain("border-l-2");
  });
});

describe("an unknown type", () => {
  it("a tree reads as nested items", () => {
    expect(treeItems(TREE)).toEqual([
      {
        label: "Bootloader",
        children: [
          { label: "Flash layout", children: [] },
          { label: "Secure boot", children: [{ label: "Key ceremony", children: [] }] },
        ],
      },
      { label: "Release", children: [] },
    ]);
  });

  it("a tree draws as a nested list, with its title", () => {
    const html = draw(TREE);
    expect(html).not.toMatch(/unsupported/i);
    expect(html).toContain('data-genui-fallback="tree"');
    expect(html).toContain("Firmware");
    expect(html).toMatch(/<li>Secure boot<div[^>]*><ul[^>]*><li>Key ceremony<\/li><\/ul><\/div><\/li>/);
  });

  it("a tree in the node's own children reads too", () => {
    expect(treeItems({ type: "treeView", children: [{ label: "A", items: ["B"] }] })).toEqual([
      { label: "A", children: [{ label: "B", children: [] }] },
    ]);
  });

  it("any other type draws a neutral card with its words", () => {
    const html = draw({ type: "gauge", props: { title: "Velocity", text: "12 points this week" } });
    expect(html).not.toMatch(/unsupported/i);
    expect(html).toContain("This card could not be shown.");
    expect(html).toContain("Velocity");
    expect(html).toContain("12 points this week");
    expect(fallbackLines({ type: "gauge", props: { title: "Velocity", text: "12 points this week" } })).toEqual([
      "Velocity",
      "12 points this week",
    ]);
  });

  it("an unknown template draws the same neutral card, not \"unknown template\"", () => {
    const html = renderToStaticMarkup(renderTemplate("ganttChart", { title: "Plan", rows: [{ label: "Bootloader" }] }));
    expect(html).not.toMatch(/unknown template/i);
    expect(html).toContain("This card could not be shown.");
    expect(html).toContain("Bootloader");
  });

  it("an unknown type with no words draws nothing", () => {
    expect(draw({ type: "gauge", props: { size: 3 } })).toBe("");
  });

  it("the fallback never draws raw markup", () => {
    const html = draw({ type: "gauge", props: { title: "<img src=x onerror=alert(1)>" } });
    expect(html).not.toContain("<img");
  });
});
