/**
 * One priority system in Projects and My Tasks (D78, owner, 2026-09-24).
 *
 * Projects had its own 0-3 scale. My Tasks had the matrix (important ×
 * urgent × leveraged). One task sits in both apps (D53), so one task had two
 * priorities. D78 retires the scale. Projects now shows the matrix level,
 * and Important and Leveraged are one shared answer on the task:
 *
 *   • Important is `pm_tasks.importance >= IMPORTANT_AT` (2).
 *   • Leveraged is `pm_tasks.leveraged`.
 *   • Urgent comes from the due date.
 *
 * This file is the fence (R7). It reads SOURCE as well as values, because
 * the table cell and the bulk bar are `.tsx`, which vitest never collects
 * (D-PM-21).
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CELL_META,
  CELLS_IN_ORDER,
  IMPORTANT_AT,
  importantFromImportance,
  type PriorityCell,
} from "@/app/tasks/lib/priority";

import type { TaskRow } from "./api";
import { cardChips } from "./card";
import { groupTasks } from "./grouping";
import { MATRIX_LEVELS, cellLabel, taskCell } from "./matrix";

const PROJECTS = fileURLToPath(new URL("..", import.meta.url));

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.ts$/.test(name)) out.push(path);
  }
  return out;
}

const raw = (path: string) => readFileSync(path, { encoding: "utf-8" });

/** Code only. A comment that explains the rule may name a level. */
const code = (path: string) =>
  raw(path)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");

const rel = (path: string) => path.replace(/\\/g, "/").split("/projects/")[1];

const read = (relPath: string) => code(join(PROJECTS, relPath));

const NOW = Date.parse("2026-09-24T12:00:00Z");
const hours = (n: number) => new Date(NOW + n * 3_600_000).toISOString();

const row = (over: Partial<TaskRow> = {}): TaskRow => ({
  id: "t1",
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s1",
  title: "Ship it",
  ...over,
});

describe("the retired 0-3 scale is gone from Projects (D78)", () => {
  it("has no source file that names it", () => {
    // Comments too. A comment that still names the old top level teaches
    // the next reader a scale that does not exist.
    const offenders = sources(PROJECTS).filter((f) =>
      /Highest|IMPORTANCE_OPTIONS|importanceLabel/.test(raw(f))
    );
    expect(offenders.map(rel)).toEqual([]);
  });

  it("spells no matrix level label as a literal", () => {
    // Every label comes from CELL_META through lib/matrix.ts. A literal is
    // a second copy, and a second copy drifts. "Important" is left out,
    // because it is also the name of a flag in MATRIX_FLAG_OPTIONS.
    const labels = CELLS_IN_ORDER.map((cell) => CELL_META[cell].label).filter(
      (label) => label !== "Important"
    );
    const pattern = new RegExp(`["'\`](${labels.join("|")})["'\`]`);
    const offenders = sources(PROJECTS).filter((f) => pattern.test(code(f)));
    expect(offenders.map(rel)).toEqual([]);
  });
});

describe("every Projects surface derives the level from lib/matrix.ts", () => {
  it("imports the matrix in each surface that shows or writes a priority", () => {
    for (const file of [
      "lib/card.ts",
      "lib/grouping.ts",
      "lib/board.ts",
      "lib/quickAdd.ts",
      "lib/selection.ts",
      "components/TableView.tsx",
      "components/BulkBar.tsx",
    ]) {
      expect(read(file), file).toMatch(/from "(\.\.\/lib|\.)\/matrix"/);
    }
  });

  it("draws the table cell as THE priority chip, labelled with the level", () => {
    // One chip in both apps (2026-09-24): the table draws the same
    // `PriorityChip` My Tasks' Priority column draws, not bare text.
    expect(read("components/TableView.tsx")).toMatch(
      /<PriorityChip chip=\{priorityChip\(taskCell\(task\)\)\} \/>/,
    );
  });

  it("builds the table editor and the bulk bar from lib/matrix.ts", () => {
    expect(read("components/TableView.tsx")).toMatch(/MATRIX_FLAG_OPTIONS/);
    // The bulk bar sets ONE flag per action (review 2026-09-24).
    expect(read("components/BulkBar.tsx")).toMatch(/BULK_FLAG_OPTIONS/);
  });

  it("gives the card chip the level label for the task", () => {
    const cases: Partial<TaskRow>[] = [
      { importance: 2, leveraged: true, due_at: hours(4) },
      { importance: 3, due_at: hours(-1) },
      { importance: 2, leveraged: true },
      { importance: 2 },
      { leveraged: true, due_at: hours(4) },
      { leveraged: true },
    ];
    const seen = new Set<string>();
    for (const over of cases) {
      const task = row(over);
      const cell = taskCell(task, NOW);
      seen.add(cell);
      const chip = cardChips(task, NOW).find((c) => c.key === "importance");
      expect(chip?.label, cell).toBe(CELL_META[cell].label);
    }
    // The six cases reach the six levels that draw a chip.
    expect(seen.size).toBe(6);
  });

  it("groups by level keys only, in rank order", () => {
    const tasks = [
      row({ id: "a" }),
      row({ id: "b", leveraged: true }),
      row({ id: "c", importance: 2 }),
      row({ id: "d", importance: 2, leveraged: true }),
    ];
    const groups = groupTasks(tasks, "importance", { statuses: [] });
    const keys = groups.map((g) => g.key);
    expect(keys).toHaveLength(4);
    for (const key of keys) expect(CELLS_IN_ORDER).toContain(key);
    const ranks = keys.map((k) => CELLS_IN_ORDER.indexOf(k as PriorityCell));
    expect(ranks).toEqual([...ranks].sort((x, y) => x - y));
    for (const group of groups) {
      expect(group.label).toBe(cellLabel(group.key as PriorityCell));
    }
  });

  it("orders the levels exactly as My Tasks does", () => {
    expect(MATRIX_LEVELS).toEqual(CELLS_IN_ORDER);
  });
});

describe("where the two apps meet", () => {
  it("reads Important as importance 2 or more", () => {
    // R7. The gateway holds the same number, and test_priority_shared.py
    // keeps the two equal.
    expect(IMPORTANT_AT).toBe(2);
  });

  it("maps the stored integer to Important, and NULL to 'not judged'", () => {
    expect(importantFromImportance(3)).toBe(true);
    expect(importantFromImportance(2)).toBe(true);
    expect(importantFromImportance(1)).toBe(false);
    expect(importantFromImportance(0)).toBe(false);
    expect(importantFromImportance(null)).toBeUndefined();
    expect(importantFromImportance(undefined)).toBeUndefined();
  });
});
