/**
 * E7a fence (`ai_studio.md` §14, row E7a): a pane of the workflow editor fills
 * a phone, and keeps its desktop width without `fill`.
 *
 * The palette, the Copilot and the inspector each take an optional `fill`. A
 * phone passes it, and the pane takes the whole width. Without it, the root
 * class of each pane is the class it had before E7a, byte for byte, so the
 * desktop at 768 px and up draws the same.
 *
 * Mutation 1: drop the `fill` branch in `NodePalette`. "fills the width"
 * turns red for the palette.
 * Mutation 2: write `w-60` in place of `sm:w-60` in the palette. "keeps the
 * desktop class" turns red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import CopilotPanel from "./CopilotPanel";
import NodeInspector from "./NodeInspector";
import NodePalette from "./NodePalette";
import type { Catalog, WorkflowGraphNode } from "../lib/types";

const catalog: Catalog = {
  node_types: [],
  condition_ops: ["equals"],
  agents: [],
  integrations: [],
  tools: [],
  modules: [],
};

const approval: WorkflowGraphNode = {
  id: "approval_1",
  type: "approval",
  position: { x: 0, y: 0 },
  data: { label: "Approve", config: { message: "" } },
};

/** The class of the first element of the markup: the root of the pane. */
const rootClass = (html: string): string => /^<\w+ class="([^"]*)"/.exec(html)?.[1] ?? "";

const palette = (fill?: boolean) =>
  rootClass(renderToStaticMarkup(createElement(NodePalette, { catalog, onAdd: () => {}, fill })));

const copilot = (fill?: boolean) =>
  rootClass(
    renderToStaticMarkup(
      createElement(CopilotPanel, {
        workflowId: "wf1",
        onApplyGraph: () => {},
        onUndo: () => {},
        canUndo: false,
        onModulesCreated: () => {},
        fill,
      }),
    ),
  );

const inspector = (node: WorkflowGraphNode | null, fill?: boolean) =>
  rootClass(
    renderToStaticMarkup(
      createElement(NodeInspector, {
        node,
        catalog: null,
        upstreamIds: [],
        issues: [],
        triggerKinds: [],
        onConfig: () => {},
        onLabel: () => {},
        onDelete: () => {},
        fill,
      }),
    ),
  );

/** The root class of each pane before E7a. A desktop must draw these. */
const DESKTOP = {
  palette: "w-52 sm:w-60 shrink-0 overflow-y-auto scrollbar-thin p-2",
  copilot: "w-52 sm:w-60 shrink-0 flex flex-col",
  inspectorEmpty: "w-64 xl:w-72 shrink-0 border-l border-border p-4",
  inspector: "w-64 xl:w-72 shrink-0 border-l border-border overflow-y-auto scrollbar-thin",
};

describe("a pane with no fill keeps the desktop class, byte for byte", () => {
  it("the palette", () => {
    expect(palette()).toBe(DESKTOP.palette);
    expect(palette(false)).toBe(DESKTOP.palette);
  });

  it("the Copilot", () => {
    expect(copilot()).toBe(DESKTOP.copilot);
    expect(copilot(false)).toBe(DESKTOP.copilot);
  });

  it("the inspector, with no step and with a step", () => {
    expect(inspector(null)).toBe(DESKTOP.inspectorEmpty);
    expect(inspector(approval)).toBe(DESKTOP.inspector);
    expect(inspector(approval, false)).toBe(DESKTOP.inspector);
  });
});

describe("a pane with fill fills the width of a phone", () => {
  const panes: [string, string][] = [
    ["the palette", palette(true)],
    ["the Copilot", copilot(true)],
    ["the inspector with no step", inspector(null, true)],
    ["the inspector with a step", inspector(approval, true)],
  ];

  it.each(panes)("%s fills the width", (_name, cls) => {
    const words = cls.split(/\s+/);
    expect(words).toContain("w-full");
    expect(words).toContain("min-w-0");
    expect(words).not.toContain("shrink-0");
    for (const fixed of ["w-52", "sm:w-60", "w-64", "xl:w-72", "border-l"]) {
      expect(words, fixed).not.toContain(fixed);
    }
  });
});
