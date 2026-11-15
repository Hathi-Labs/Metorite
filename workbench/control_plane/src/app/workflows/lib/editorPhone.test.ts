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
 *
 * E7e (task 3373) adds the width of the editor's own box. With the shell
 * sidebar open, a 1024 px window left the editor 744 px and the canvas 247 px.
 * Mutation 5: make `layoutFor` return "phone" for a wide box. "a wide box
 * draws the three panes" turns red.
 * Mutation 6: measure below `if (loadError)`. "measures the editor box above
 * the early returns" turns red.
 * Mutation 7: drop `pointer-coarse:` from the ring of the "+". "a mouse gets
 * no new rule" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_PHONE_TAB,
  PHONE_TABS,
  WIDE_EDITOR_MIN_PX,
  layoutFor,
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

describe("the layout of the editor (E7e)", () => {
  it("is the phone layout on a phone, at any width", () => {
    for (const w of [null, 0, 390, WIDE_EDITOR_MIN_PX, 1440]) {
      expect(layoutFor(true, w)).toBe("phone");
    }
  });

  it("draws the three panes in a wide box", () => {
    expect(layoutFor(false, WIDE_EDITOR_MIN_PX)).toBe("wide");
    expect(layoutFor(false, 1160)).toBe("wide");
    expect(layoutFor(false, 1360)).toBe("wide");
  });

  it("takes the phone layout in a box narrower than the three panes need", () => {
    // 768 px and 1024 px windows with the shell sidebar open, measured.
    expect(layoutFor(false, 488)).toBe("phone");
    expect(layoutFor(false, 744)).toBe("phone");
    expect(layoutFor(false, WIDE_EDITOR_MIN_PX - 1)).toBe("phone");
  });

  it("keeps the wide layout until the box is measured, as before E7e", () => {
    expect(layoutFor(false, null)).toBe("wide");
  });

  it("leaves the canvas 300 px or more beside the two side panes", () => {
    // The palette column 241 px, the inspector 288 px from a 1280 px window.
    expect(WIDE_EDITOR_MIN_PX - 241 - 288).toBeGreaterThanOrEqual(300);
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

  it("measures the editor box above the early returns (E7e)", () => {
    const measure = src.indexOf("useContainerWidth(editorBox)");
    const layout = src.indexOf("layoutFor(isMobile, editorWidth)");
    expect(measure).toBeGreaterThan(0);
    expect(layout).toBeGreaterThan(0);
    expect(measure).toBeLessThan(firstReturn);
    expect(layout).toBeLessThan(firstReturn);
    // The loader and the editor carry the ref, so the width is known before the editor draws.
    expect(src.match(/<div ref=\{setEditorBox\}/g) ?? []).toHaveLength(2);
    // The layout decides each phone branch. The viewport alone decides none.
    expect(src.match(/\bisMobile\b/g) ?? []).toHaveLength(2);
  });

  it("asks the model which panes draw, and fills a phone with each pane", () => {
    expect(src).toContain("visiblePanes(phone, phoneTab, leftTab)");
    expect(src).toContain("{canvasShown && <div");
    expect(src).toContain("{inspectorShown && (");
    // The palette, the Copilot and the inspector.
    expect(src.match(/fill=\{phone\}/g) ?? []).toHaveLength(3);
  });

  it("keeps the run console shut on a phone until Test or the member opens it", () => {
    expect(src).toContain("collapsed={runConsoleCollapsed(consoleCollapsed, phone)}");
  });

  it("opens the trigger panel below the two toolbar rows of the phone layout (E7e)", () => {
    expect(src).toContain("phone={phone}");
  });

  it("names the workflow name box for a screen reader (E7e)", () => {
    expect(src).toContain('aria-label="Workflow name"');
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

describe("the + on a wire (E7e)", () => {
  const src = readFileSync(new URL("../components/WorkflowEdge.tsx", import.meta.url), "utf8");
  const cls = /className=\{`(grid place-items-center w-4 h-4[^$`]*)\$\{/.exec(src)?.[1] ?? "";

  it("keeps the 16 px dot", () => {
    expect(cls.split(/\s+/)).toEqual(expect.arrayContaining(["w-4", "h-4", "rounded-full"]));
  });

  it("gets a ring of 32 px or more under a finger: 14 px inside the border and 10 px on each side", () => {
    expect(cls).toContain("pointer-coarse:before:-inset-2.5");
    expect(cls).toContain("pointer-coarse:before:absolute");
    expect(cls).toContain("pointer-coarse:relative");
  });

  it("a mouse gets no new rule", () => {
    const before = "grid place-items-center w-4 h-4 rounded-full border bg-background tech-transition";
    const added = cls.trim().split(/\s+/).slice(before.split(" ").length);
    expect(cls.startsWith(before)).toBe(true);
    expect(added.length).toBeGreaterThan(0);
    for (const word of added) expect(word, word).toMatch(/^pointer-coarse:/);
  });
});
