/**
 * The My Tasks prioritisation engine.
 *
 * ⚠️ This file did not exist until 2026-09-23. `priority.ts` calls itself
 * "pure + unit-testable; imported everywhere the UI slices by priority", and
 * nothing tested it. `priorityInputs` is the one function every surface
 * reads, so the formula it feeds is pinned here.
 *
 * D78 (2026-09-24) retired the org-priority seed. Important is one shared
 * answer on the task, `pm_tasks.importance >= IMPORTANT_AT`, so there is
 * nothing left to seed.
 */

import { describe, expect, it } from "vitest";

import {
  CELL_ICON_NAME,
  CELLS_IN_ORDER,
  IMPORTANT_AT,
  actionMode,
  cellForInputs,
  importanceFor,
  importantFromImportance,
  isUntagged,
  isUrgent,
  modeSuggestion,
  priorityCell,
  priorityInputs,
} from "./priority";

const NOW = Date.parse("2026-09-23T12:00:00Z");
const HOUR = 60 * 60 * 1000;
const inHours = (h: number) => new Date(NOW + h * HOUR).toISOString();

describe("the matrix formula", () => {
  it("maps the three booleans to the seven cells", () => {
    const cell = (important: boolean, urgent: boolean, leveraged: boolean) =>
      cellForInputs({ important, urgent, leveraged });
    expect(cell(true, true, true)).toBe("critical");
    expect(cell(true, true, false)).toBe("urgent");
    expect(cell(true, false, true)).toBe("high-leverage");
    expect(cell(true, false, false)).toBe("important");
    expect(cell(false, true, true)).toBe("quick-leverage");
    expect(cell(false, false, true)).toBe("speculative-bet");
    // Not important to you: urgent-only and neither fold into one level.
    expect(cell(false, true, false)).toBe("low-priority");
    expect(cell(false, false, false)).toBe("low-priority");
  });

  it("derives urgent from the due date, inside the window or overdue", () => {
    expect(isUrgent({ dueAt: inHours(24) }, 48, NOW)).toBe(true);
    expect(isUrgent({ dueAt: inHours(-5) }, 48, NOW)).toBe(true);
    expect(isUrgent({ dueAt: inHours(72) }, 48, NOW)).toBe(false);
    expect(isUrgent({ dueAt: undefined }, 48, NOW)).toBe(false);
  });
});

describe("Important is the shared answer on the task (D78)", () => {
  it("is importance 2 and up, and NULL means nobody judged it", () => {
    expect(IMPORTANT_AT).toBe(2);
    expect([3, 2, 1, 0].map(importantFromImportance)).toEqual([true, true, false, false]);
    expect(importantFromImportance(null)).toBeUndefined();
    expect(importantFromImportance(undefined)).toBeUndefined();
  });

  it("writes 2 for Important and 0 for 'judged, not important'", () => {
    // 0 is a decision. NULL is the absence of one. The two must not merge.
    expect(importanceFor(true)).toBe(2);
    expect(importanceFor(false)).toBe(0);
    for (const value of [true, false]) {
      expect(importantFromImportance(importanceFor(value))).toBe(value);
    }
  });

  it("reads Important straight, with no seed from anything else", () => {
    expect(
      priorityInputs({ dueAt: undefined, important: undefined, leveraged: false }, 48, NOW)
        .important,
    ).toBe(false);
    expect(
      priorityInputs({ dueAt: undefined, important: false, leveraged: false }, 48, NOW)
        .important,
    ).toBe(false);
    expect(
      priorityInputs({ dueAt: undefined, important: true, leveraged: false }, 48, NOW)
        .important,
    ).toBe(true);
  });

  it("leaves an unjudged task in Low Priority until somebody flags it", () => {
    const task = { dueAt: inHours(200), leveraged: false };
    expect(priorityCell({ ...task, important: undefined }, 48, NOW)).toBe("low-priority");
    expect(
      priorityCell({ ...task, important: importantFromImportance(2) }, 48, NOW),
    ).toBe("important");
  });

  it("still combines with the DERIVED urgency, from the shared due date", () => {
    // The due date sets urgency. An Important task due tomorrow reaches the
    // top half of the matrix.
    const task = { dueAt: inHours(20), important: true, leveraged: false };
    expect(priorityCell(task, 48, NOW)).toBe("urgent");
    expect(actionMode(task, 48, NOW)).toBe("delegate");
  });

  it("keeps an unjudged task UNTAGGED, so it still asks for a call", () => {
    expect(isUntagged({ important: undefined, leveraged: undefined })).toBe(true);
    expect(isUntagged({ important: true, leveraged: undefined })).toBe(false);
    expect(isUntagged({ important: undefined, leveraged: true })).toBe(false);
  });

  it("feeds the delegate/schedule nudge from the shared answer", () => {
    const item = {
      dueAt: inHours(200),
      important: true,
      leveraged: false,
      disposition: "NEXT" as const,
      isMine: true,
      keptMine: false,
    };
    // Important, not urgent. That is schedule, the "important" level's mode.
    expect(modeSuggestion(item, 48, NOW)).toEqual({ mode: "schedule", cell: "important" });
  });
});

describe("the level icons", () => {
  it("gives each of the seven levels its own glyph", () => {
    const icons = CELLS_IN_ORDER.map((cell) => CELL_ICON_NAME[cell]);
    expect(icons.every(Boolean)).toBe(true);
    expect(new Set(icons).size).toBe(CELLS_IN_ORDER.length);
  });
});
