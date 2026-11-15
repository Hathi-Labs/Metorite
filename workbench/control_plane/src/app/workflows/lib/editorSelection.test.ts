/**
 * E7e fence (task 3373): a tap on the palette selects the new step, and the
 * inspector shows it on a desktop too.
 *
 * Before E7e the editor passed an inline arrow to `onSelectionChange`. React
 * Flow sends its report again each time that prop changes, so the next render
 * sent "no step selected" and cleared the step that the tap had selected.
 *
 * Mutation 1: pass the inline arrow again. "passes one stable handler" turns red.
 * Mutation 2: clear on every empty report. "keeps the step on the report of a
 * canvas that mounts" turns red.
 * Mutation 3: drop `selected` from the new step in `addNode`. "selects the new
 * step on the canvas too" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { selectionAfterChange } from "./editorSelection";

describe("the step that the inspector shows", () => {
  it("shows the new step after a tap on the palette and its report", () => {
    // The tap sets the step, and the canvas then reports the same id.
    expect(selectionAfterChange("wait_1", [], ["wait_1"])).toBe("wait_1");
    expect(selectionAfterChange("wait_1", ["agent_1"], ["wait_1"])).toBe("wait_1");
  });

  it("keeps the step on the report of a canvas that mounts", () => {
    // The Canvas tab of the phone layout mounts React Flow with no selection.
    expect(selectionAfterChange("wait_1", [], [])).toBe("wait_1");
  });

  it("clears the step when the member clears the selection of the canvas", () => {
    expect(selectionAfterChange("agent_1", ["agent_1"], [])).toBeNull();
  });

  it("shows the step that the member clicks", () => {
    expect(selectionAfterChange(null, [], ["agent_1"])).toBe("agent_1");
    expect(selectionAfterChange("agent_1", ["agent_1"], ["output_1"])).toBe("output_1");
  });

  it("shows no step when two or more are selected", () => {
    expect(selectionAfterChange("agent_1", ["agent_1"], ["agent_1", "output_1"])).toBeNull();
  });
});

describe("the editor page", () => {
  const src = readFileSync(
    new URL("../../studio/workflows/[id]/page.tsx", import.meta.url),
    "utf8",
  ).replace(/\r\n/g, "\n");

  it("passes one stable handler to React Flow", () => {
    expect(src).toContain("onSelectionChange={onSelectionChange}");
    expect(src).not.toMatch(/onSelectionChange=\{\s*\(/);
    expect(src).toContain("selectionAfterChange(cur, before, after)");
    // The handler has no dependency, so it is the same function on each render.
    const start = src.indexOf("const onSelectionChange = useCallback(");
    const end = src.indexOf("\n  }, ", start);
    expect(start).toBeGreaterThan(0);
    expect(src.slice(end, src.indexOf(");", end))).toBe("\n  }, []");
  });

  it("defines the handler above the early returns", () => {
    const handler = src.indexOf("const onSelectionChange = useCallback(");
    expect(handler).toBeGreaterThan(0);
    expect(handler).toBeLessThan(src.indexOf("if (loadError) {"));
  });

  it("selects the new step on the canvas too", () => {
    expect(src).toContain("selected: i === chain.length - 1,");
    expect(src).toContain("...ns.map((n) => (n.selected ? { ...n, selected: false } : n)),");
  });
});
