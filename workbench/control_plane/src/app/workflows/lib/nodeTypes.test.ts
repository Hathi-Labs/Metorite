/**
 * WFC-4a-1 fence: each node type of the engine draws in the editor.
 *
 * A graph from the API or the Copilot can hold any type of
 * `graph.NODE_TYPES`. A type with no icon or no label drew as a bare id with
 * the fallback icon. This test reads the list from the Python source itself,
 * the idiom of `crmU17.test.ts`: a mirror in JSON would need its own fence,
 * and the source is the one list.
 *
 * Mutation: delete `human_step` from `NODE_KIND_LABEL`. "has a label" turns red.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { NODE_ICON, NODE_KIND_LABEL } from "./nodeVisuals";
import { NODE_CATEGORY_STYLE, categoryForType, type NodeType } from "./types";

const GRAPH_PY = join(
  __dirname,
  "../../../../../../apps/services/gateway/gateway/routes/workflows/engine/graph.py",
);

/** The names in `NODE_TYPES = frozenset({ ... })`, comments left out. */
function engineNodeTypes(): string[] {
  const src = readFileSync(GRAPH_PY, "utf8").replace(/\r\n/g, "\n");
  const block = src.match(/^NODE_TYPES = frozenset\(\n\s+\{\n([\s\S]*?)\n\s+\}\n\)/m);
  if (!block) throw new Error("graph.py lost its NODE_TYPES block");
  return [...block[1].matchAll(/^\s+"([a-z_]+)",\s*$/gm)].map((m) => m[1]);
}

/** The name an icon component was bound to (`themedIcon` sets it). */
function iconName(type: NodeType): string {
  const name = (NODE_ICON[type] as { displayName?: string } | undefined)?.displayName ?? "";
  return name.match(/^ThemedIcon\((.+)\)$/)?.[1] ?? "";
}

describe("each engine node type draws", () => {
  const types = engineNodeTypes();

  it("reads the whole list from graph.py", () => {
    expect(types).toContain("human_step");
    expect(types).toContain("pm_task");
    expect(types.length).toBeGreaterThanOrEqual(13);
  });

  it.each(types)("%s has an icon of the set", (type) => {
    const name = iconName(type as NodeType);
    expect(name, type).not.toBe("");
    expect(isKnownIcon(name), name).toBe(true);
  });

  it.each(types)("%s has a label", (type) => {
    expect(NODE_KIND_LABEL[type as NodeType], type).toMatch(/\S/);
  });

  it.each(types)("%s has a category with a style", (type) => {
    expect(NODE_CATEGORY_STYLE[categoryForType(type as NodeType)], type).toBeDefined();
  });

  it("puts the human step beside the other logic steps", () => {
    expect(categoryForType("human_step")).toBe("logic");
  });
});
