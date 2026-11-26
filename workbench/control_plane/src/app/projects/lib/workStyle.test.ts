/**
 * WS-39 SP-3a: the words of the work-style picker are true for the level of the
 * node, and the page treats the open picker as an overlay. The runner has no
 * DOM, so the page rules are source scans.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { pickerNote, savedStyleTitle, styleHint, workStyle } from "./workStyle";

const page = readFileSync(new URL("../page.tsx", import.meta.url), "utf-8");

describe("a space and a folder", () => {
  const sprints = workStyle("sprints");

  it.each(["space", "folder"] as const)("promise no Backlog canvas on a %s", (level) => {
    expect(styleHint(sprints, level)).toBe(
      `A ${level} shows no Backlog canvas, and the projects in it do not take this style.`,
    );
    expect(savedStyleTitle("Ops", "sprints", level)).toBe(
      `Ops now works as Sprints or Cycles. A ${level} shows no Backlog canvas. Its projects keep their own style.`,
    );
    expect(pickerNote(level)).toBe(`A change applies to this ${level} only and deletes nothing.`);
    expect(savedStyleTitle("Ops", "flow", level)).toBe("Ops now works as Flow");
  });

  it.each(["project", "subproject"] as const)("leave the words of a %s as they were", (level) => {
    expect(styleHint(sprints, level)).toBe(sprints.hint);
    expect(sprints.hint).toContain("Adds the Backlog canvas.");
    expect(savedStyleTitle("Ops", "sprints", level)).toBe("Ops now works as Sprints or Cycles");
    expect(pickerNote(level)).toContain("parts of the project");
  });

  it("get the level from the tree, as the page draws it", () => {
    expect(page).toMatch(/level=\{levelOf\(visibleRoots, workStyleFor\.id\)\}/);
    expect(page).toMatch(/title: savedStyleTitle\(/);
  });
});

describe("the open picker", () => {
  it("is an overlay, so the page keys and Escape wait", () => {
    const from = page.indexOf("const overlayOpen =");
    expect(from).toBeGreaterThan(-1);
    const list = page.slice(from, page.indexOf(";", from));
    expect(list).toContain("Boolean(workStyleFor)");
  });
});
