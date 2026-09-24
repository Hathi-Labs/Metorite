/**
 * A chat saved before S9 still renders its task cards.
 *
 * S9 (`project-docs/specs/my_tasks_cutover.md` §5) renamed the 29 chat tools
 * to `my_tasks_*`. A stored message keeps the name it was saved with, so
 * `TaskToolCards` maps each old name to its new one before it routes. This
 * suite pins that map to the skill's own tool list, so a tool the skill adds
 * or drops cannot leave the map wrong.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  LEGACY_TOOL_NAMES,
  currentToolName,
  isTaskCardTool,
} from "./TaskToolCards";

const SKILL_INIT = path.resolve(
  __dirname, "..", "..", "..", "..", "..",
  "apps", "skills", "skill-my-tasks", "skill_my_tasks", "__init__.py",
);

/** The names in the skill's `__all__`, read from the source. */
function skillTools(): string[] {
  const src = fs.readFileSync(SKILL_INIT, "utf8");
  const block = src.slice(src.indexOf("__all__"));
  return [...block.matchAll(/"(my_tasks_\w+)"/g)].map((m) => m[1]);
}

describe("TaskToolCards — the legacy tool names (S9)", () => {
  it("maps one old name to each of the skill's 29 tools", () => {
    const tools = skillTools();
    expect(tools).toHaveLength(29);
    expect(new Set(Object.values(LEGACY_TOOL_NAMES))).toEqual(new Set(tools));
    expect(Object.keys(LEGACY_TOOL_NAMES)).toHaveLength(29);
  });

  it("gives each old name the tool that keeps its suffix", () => {
    for (const [legacy, current] of Object.entries(LEGACY_TOOL_NAMES)) {
      expect(current.slice("my_tasks_".length)).toBe(
        legacy.slice(legacy.indexOf("_") + 1),
      );
      expect(currentToolName(legacy)).toBe(current);
    }
  });

  it("routes every stored old name to a card", () => {
    for (const legacy of Object.keys(LEGACY_TOOL_NAMES)) {
      expect(isTaskCardTool(currentToolName(legacy))).toBe(true);
    }
  });

  it("leaves every other name as it is", () => {
    expect(currentToolName("my_tasks_capture")).toBe("my_tasks_capture");
    expect(currentToolName("list_tasks")).toBe("list_tasks");
    expect(currentToolName("toString")).toBe("toString");
    expect(isTaskCardTool("list_tasks")).toBe(false);
  });
});
