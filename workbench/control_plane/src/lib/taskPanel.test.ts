/**
 * One docked width and one set of words for the task panel, in both apps.
 * See `taskPanel.ts`.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  PANEL_MODE_HINTS,
  PANEL_MODE_ICONS,
  PANEL_WIDTH_CLASS,
} from "@/app/projects/lib/panelMode";

import { EXPAND_ICONS, EXPAND_LABELS, TASK_PANEL_WIDTH } from "./taskPanel";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

describe("one task panel width", () => {
  it("Projects docks its panel at the shared width", () => {
    expect(PANEL_WIDTH_CLASS.side).toBe(TASK_PANEL_WIDTH);
  });

  it("My Tasks docks its detail at the shared width, in both places", () => {
    const page = read("app/tasks/page.tsx");
    expect(page).not.toMatch(/w-\[380px\]/);
    expect(
      page.match(/<aside className=\{`flex h-full w-full \$\{TASK_PANEL_WIDTH\} shrink-0/g) ?? [],
    ).toHaveLength(2);
  });
});

describe("one set of words for the expand", () => {
  it("Projects' panel modes read the shared words and glyphs", () => {
    expect(PANEL_MODE_HINTS).toBe(EXPAND_LABELS);
    expect(PANEL_MODE_ICONS).toBe(EXPAND_ICONS);
  });
});
