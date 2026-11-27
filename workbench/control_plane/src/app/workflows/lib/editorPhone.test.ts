/**
 * E7a fence (`ai_studio.md` §14, row E7a): the workflow editor on a phone.
 *
 * A desktop draws the left column, the canvas and the inspector side by side.
 * A phone draws ONE pane under a segmented tab bar, the shape of the Studio
 * IDE on a phone. From 768 px up the desktop tree stays as it was.
 *
 * Mutation 1: make `visiblePanes` return one pane on a desktop. "a desktop
 * draws the three panes" turns red.
 * Mutation 2: return two panes on a phone. "a phone draws exactly one" turns red.
 * Mutation 3: call `useViewMode()` below `if (loadError)` in the page. "reads
 * the view mode above the early returns" turns red.
 * Mutation 4: change the class of the desktop canvas. "keeps the desktop
 * markup" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_PHONE_TAB,
  PHONE_TABS,
  runConsoleCollapsed,
  leftTabFor,
  phoneTabAfter,
  visiblePanes,
  type LeftTab,
  type PhoneTab,
} from "./editorPhone";

const TABS: PhoneTab[] = ["palette", "canvas", "step", "copilot"];
const LEFT: LeftTab[] = ["palette", "copilot"];

describe("the panes that draw", () => {
  it.each(LEFT)("a desktop draws the three panes, with %s in the left column", (left) => {
    for (const tab of TABS) {
      expect(visiblePanes(false, tab, left)).toEqual([left, "canvas", "inspector"]);
    }
  });

  it.each(TABS)("a phone draws exactly one pane on the %s tab", (tab) => {
    for (const left of LEFT) {
      expect(visiblePanes(true, tab, left)).toHaveLength(1);
    }
  });

  it("a phone draws the pane of its tab", () => {
    expect(visiblePanes(true, "palette", "palette")).toEqual(["palette"]);
    expect(visiblePanes(true, "canvas", "palette")).toEqual(["canvas"]);
    expect(visiblePanes(true, "step", "palette")).toEqual(["inspector"]);
    expect(visiblePanes(true, "copilot", "copilot")).toEqual(["copilot"]);
  });
});

describe("the phone tabs", () => {
  it("are Palette, Canvas, Step and Copilot, in that order", () => {
    expect(PHONE_TABS.map((t) => t.id)).toEqual(TABS);
    expect(PHONE_TABS.map((t) => t.label)).toEqual(["Palette", "Canvas", "Step", "Copilot"]);
  });

  it("open on the canvas", () => {
    expect(DEFAULT_PHONE_TAB).toBe("canvas");
  });

  it("set the left column to the tab of the palette or the Copilot", () => {
    expect(leftTabFor("palette", "copilot")).toBe("palette");
    expect(leftTabFor("copilot", "palette")).toBe("copilot");
    expect(leftTabFor("canvas", "copilot")).toBe("copilot");
    expect(leftTabFor("step", "palette")).toBe("palette");
  });
});

describe("the tab after an act", () => {
  it("opens the Step tab after a tap on a block adds it", () => {
    expect(phoneTabAfter("add")).toBe("step");
  });

  it("opens the palette after a + on a wire", () => {
    expect(phoneTabAfter("arm")).toBe("palette");
  });

  it("opens the Copilot after the canvas prompt sends its words", () => {
    expect(phoneTabAfter("generate")).toBe("copilot");
  });

  it("opens the canvas after Test, so the run shows on the graph", () => {
    expect(phoneTabAfter("test")).toBe("canvas");
  });
});

describe("the run console", () => {
  it("is open on a desktop and shut on a phone until the member chooses", () => {
    expect(runConsoleCollapsed(null, false)).toBe(false);
    expect(runConsoleCollapsed(null, true)).toBe(true);
  });

  it("follows the choice of the member, or of Test, on both", () => {
    expect(runConsoleCollapsed(false, true)).toBe(false);
    expect(runConsoleCollapsed(true, false)).toBe(true);
  });
});

describe("the editor page", () => {
  const src = readFileSync(
    new URL("../../studio/workflows/[id]/page.tsx", import.meta.url),
    "utf8",
  ).replace(/\r\n/g, "\n");
  const firstReturn = src.indexOf("if (loadError) {");

  it("reads the view mode above the early returns", () => {
    expect(firstReturn).toBeGreaterThan(0);
    const hook = src.indexOf("useViewMode()");
    expect(hook).toBeGreaterThan(0);
    expect(hook).toBeLessThan(firstReturn);
    expect(src.indexOf("useState<PhoneTab>(DEFAULT_PHONE_TAB)")).toBeLessThan(firstReturn);
    expect(src.indexOf("useState<PhoneTab>(DEFAULT_PHONE_TAB)")).toBeGreaterThan(0);
  });

  it("asks the model which panes draw, and fills a phone with each pane", () => {
    expect(src).toContain("visiblePanes(isMobile, phoneTab, leftTab)");
    expect(src).toContain("{canvasShown && <div");
    expect(src).toContain("{inspectorShown && (");
    // The palette, the Copilot and the inspector.
    expect(src.match(/fill=\{isMobile\}/g) ?? []).toHaveLength(3);
  });

  it("keeps the run console shut on a phone until Test or the member opens it", () => {
    expect(src).toContain("collapsed={runConsoleCollapsed(consoleCollapsed, isMobile)}");
  });

  it("draws the phone tabs with the shared segmented bar", () => {
    expect(src).toMatch(/<Tabs\s+tabs=\{PHONE_TABS\}[^>]*variant="segmented"[^>]*chrome="inline"/);
  });

  it("keeps the desktop markup of the panes and the toolbar", () => {
    for (const cls of [
      '"flex-1 min-h-0 flex"',
      '"flex flex-col shrink-0 border-r border-border"',
      'className="flex-1 min-w-0 relative"',
      '"flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-border shrink-0"',
      '"ml-auto flex items-center gap-1.5 shrink-0"',
    ]) {
      expect(src, cls).toContain(cls);
    }
  });
});

describe("the handles of a step", () => {
  const src = readFileSync(new URL("../components/WorkflowNode.tsx", import.meta.url), "utf8");

  it("grow to 20 px under a finger, each of the three", () => {
    // `handleCls` serves the target and the source. The yes and the no of a condition have their own.
    const small = src.match(/!w-3 !h-3/g) ?? [];
    const coarse = src.match(/!w-3 !h-3[^"]*pointer-coarse:!w-5 pointer-coarse:!h-5/g) ?? [];
    expect(small).toHaveLength(3);
    expect(coarse).toHaveLength(3);
  });
});
