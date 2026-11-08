/**
 * STU-G3a fix 1 fence: a click on an Action, a Workflow or the Skill in the Explorer shows that item
 * in the Code tab, and not the top of the JSON.
 *
 * Mutation 1: make `findLine` ignore the section. "looks after the section key" turns red.
 * Mutation 2: drop `reveal` from the action nodes of `buildOutline`. "gives each node a way to find it" turns red.
 */
import { describe, expect, it } from "vitest";

import { buildOutline, type OutlineNode } from "./buildOutline";
import { findLine } from "./codeReveal";

const manifest = {
  studio: 1, id: "t", name: "T", purpose: "p",
  objects: [{ name: "m", fields: [{ key: "id", type: "text" }] }],
  actions: [{ id: "weekly", uses: "object:m.list" }, { id: "second", uses: "object:m.get" }],
  workflows: [{ id: "weekly" }],
  skill: { description: "Answers" },
};
const text = JSON.stringify(manifest, null, 2);
const lines = text.split("\n");
const all = (n: OutlineNode): OutlineNode[] => [n, ...(n.children ?? []).flatMap(all)];

describe("findLine", () => {
  it("looks after the section key, so an action and a workflow of one id differ", () => {
    const a = findLine(text, { section: "actions", needle: '"id": "weekly"' })!;
    const w = findLine(text, { section: "workflows", needle: '"id": "weekly"' })!;
    expect(a).toBeLessThan(w);
    expect(lines[a - 1]).toContain('"id": "weekly"');
    expect(lines.slice(0, w - 1).some((l) => l.includes('"workflows"'))).toBe(true);
  });
  it("finds the skill block, and gives null for a miss", () => {
    expect(lines[findLine(text, { section: "skill", needle: '"skill"' })! - 1]).toContain('"skill"');
    expect(findLine(text, { section: "actions", needle: '"id": "nope"' })).toBeNull();
    expect(findLine("", { section: "x", needle: "y" })).toBeNull();
  });
});

describe("the nodes of the Explorer", () => {
  it("gives each node a way to find it in the Code tab", () => {
    const nodes = all(buildOutline(manifest).root!);
    for (const kind of ["action", "workflow", "skill"] as const) {
      const n = nodes.find((x) => x.kind === kind)!;
      expect(n.tab, kind).toBe("code");
      expect(n.reveal, kind).toBeTruthy();
      expect(findLine(text, n.reveal!), kind).not.toBeNull();
    }
    const second = nodes.find((x) => x.label === "second")!;
    expect(lines[findLine(text, second.reveal!)! - 1]).toContain('"id": "second"');
  });
});
