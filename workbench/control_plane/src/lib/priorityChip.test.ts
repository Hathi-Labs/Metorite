/**
 * THE priority chip (D78, 2026-09-24) — one chip in both task apps.
 *
 * Until then a level was drawn three ways: a raw-palette pill in My Tasks
 * (`PriorityBadge`'s `CELL_TONE`), tinted text on the Projects card, and bare
 * text in the Projects table. Now `priorityChip(cell)` in `taskCard.ts` is the
 * descriptor and `PriorityChip` in `components/TaskMeta.tsx` draws it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { importanceChip } from "@/app/projects/lib/card";
import { CELLS_IN_ORDER, type PriorityCell } from "@/app/tasks/lib/priority";

import { PRIORITY_CHIP_STYLE, priorityChip } from "./taskCard";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

const TONE_ORDER = ["danger", "warning", "muted"];
const RANK_ORDER = ["strong", "soft", "faint"];
const weight = (cell: PriorityCell) => {
  const { tone, rank } = PRIORITY_CHIP_STYLE[cell];
  return TONE_ORDER.indexOf(tone) * 10 + RANK_ORDER.indexOf(rank);
};

describe("the priority chip", () => {
  it("gives each of the seven levels its own look and its own glyph", () => {
    expect(CELLS_IN_ORDER).toHaveLength(7);
    const looks = CELLS_IN_ORDER.map((c) => {
      const { tone, rank } = PRIORITY_CHIP_STYLE[c];
      return `${tone}/${rank}`;
    });
    expect(new Set(looks).size).toBe(7);
    expect(new Set(CELLS_IN_ORDER.map((c) => priorityChip(c).icon)).size).toBe(7);
  });

  it("reads in rank order: each level is quieter than the one above it", () => {
    const weights = CELLS_IN_ORDER.map(weight);
    for (let i = 1; i < weights.length; i++) {
      expect(weights[i], CELLS_IN_ORDER[i]).toBeGreaterThan(weights[i - 1]);
    }
  });

  it("never paints a level in the member's accent", () => {
    for (const cell of CELLS_IN_ORDER) {
      expect(PRIORITY_CHIP_STYLE[cell].tone).not.toBe("accent");
    }
  });

  it("is the chip the Projects card draws", () => {
    expect(importanceChip({ importance: 2, leveraged: true, due_at: null })).toEqual(
      priorityChip("high-leverage"),
    );
    expect(priorityChip("critical")).toMatchObject({
      key: "importance",
      label: "Critical",
      tone: "danger",
      rank: "strong",
      title: "Priority: Critical",
    });
  });

  it("My Tasks' PriorityBadge is a thin wrapper over it, with no palette of its own", () => {
    const src = read("app/tasks/components/PriorityControls.tsx");
    expect(src).toMatch(
      /return <PriorityChip chip=\{priorityChip\(cell\)\} showLabel=\{showLabel\} \/>;/,
    );
    expect(src).not.toMatch(/CELL_TONE/);
    expect(read("app/projects/lib/card.ts")).not.toMatch(/CELL_TONE/);
  });

  it("TaskMeta draws a ranked chip the way PriorityChip does", () => {
    const src = read("components/TaskMeta.tsx");
    // One class function for the row and the standalone chip.
    expect(src).toMatch(/<span key=\{chip\.key\} title=\{chip\.title\} className=\{chipClass\(chip\)\}>/);
    expect(src).toMatch(/<span title=\{chip\.title\} className=\{chipClass\(chip\)\}>/);
    expect(src).toMatch(/RANKED\[chip\.tone\]\[chip\.rank\]/);
  });
});
