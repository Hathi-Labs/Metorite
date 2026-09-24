/**
 * Projects · the priority matrix on a task row (D78).
 *
 * `lib/matrix.ts` adapts a `TaskRow` to the My Tasks matrix. These tests pin
 * the adapter: the stored flags go in, the level comes out, and a flag choice
 * goes back to the task PATCH without loss.
 */
import { describe, expect, it } from "vitest";

import { CELLS_IN_ORDER, cellForInputs, type PriorityCell } from "@/app/tasks/lib/priority";

import {
  MATRIX_FLAG_OPTIONS,
  type MatrixFlags,
  flagsForCell,
  flagsOf,
  flagsPatch,
  matrixOf,
  taskCell,
} from "./matrix";

const NOW = Date.parse("2026-09-24T12:00:00Z");
const hours = (n: number) => new Date(NOW + n * 3_600_000).toISOString();

describe("matrixOf", () => {
  it("reads Important from importance 2 and up", () => {
    expect(matrixOf({ importance: 3 }).important).toBe(true);
    expect(matrixOf({ importance: 2 }).important).toBe(true);
    expect(matrixOf({ importance: 1 }).important).toBe(false);
    expect(matrixOf({ importance: 0 }).important).toBe(false);
  });

  it("keeps an unjudged task undefined, not false", () => {
    // The triage nudge looks for a task that nobody has judged.
    expect(matrixOf({ importance: null }).important).toBeUndefined();
    expect(matrixOf({}).important).toBeUndefined();
  });

  it("reads Leveraged as a plain boolean", () => {
    expect(matrixOf({ leveraged: true }).leveraged).toBe(true);
    expect(matrixOf({ leveraged: false }).leveraged).toBe(false);
    expect(matrixOf({ leveraged: null }).leveraged).toBe(false);
    expect(matrixOf({}).leveraged).toBe(false);
  });

  it("passes the due date through, and null as undefined", () => {
    expect(matrixOf({ due_at: "2026-10-01" }).dueAt).toBe("2026-10-01");
    expect(matrixOf({ due_at: null }).dueAt).toBeUndefined();
  });
});

describe("flagsOf and flagsPatch", () => {
  it("round-trips every choice through the PATCH and back", () => {
    for (const { value } of MATRIX_FLAG_OPTIONS) {
      expect(flagsOf(flagsPatch(value)), value || "(none)").toBe(value);
    }
  });

  it("always states both fields, so a choice clears what it does not set", () => {
    expect(flagsPatch("")).toEqual({ importance: 0, leveraged: false });
    expect(flagsPatch("important")).toEqual({ importance: 2, leveraged: false });
    expect(flagsPatch("leveraged")).toEqual({ importance: 0, leveraged: true });
    expect(flagsPatch("both")).toEqual({ importance: 2, leveraged: true });
  });

  it("reads an unjudged row as not flagged", () => {
    expect(flagsOf({ importance: null, leveraged: null })).toBe("");
    expect(flagsOf({})).toBe("");
    expect(flagsOf({ importance: 1 })).toBe("");
    expect(flagsOf({ importance: 3, leveraged: true })).toBe("both");
  });
});

describe("flagsForCell", () => {
  const EXPECTED: Record<PriorityCell, MatrixFlags> = {
    critical: "both",
    urgent: "important",
    "high-leverage": "both",
    important: "important",
    "quick-leverage": "leveraged",
    "speculative-bet": "leveraged",
    "low-priority": "",
  };

  it("gives the flags each of the seven levels implies", () => {
    for (const cell of CELLS_IN_ORDER) {
      expect(flagsForCell(cell), cell).toBe(EXPECTED[cell]);
    }
  });

  it("lands a task in its own level once the due date agrees", () => {
    // Urgent is the due date's to set. With a close due date, the urgent
    // levels come back. Without one, the non-urgent levels come back.
    for (const cell of CELLS_IN_ORDER) {
      const patch = flagsPatch(flagsForCell(cell));
      const urgent = cell === "critical" || cell === "urgent" || cell === "quick-leverage";
      const task = { ...patch, due_at: urgent ? hours(4) : undefined };
      expect(taskCell(task, NOW), cell).toBe(cell);
    }
  });

  it("gives no flags for a key that is not a level", () => {
    expect(flagsForCell("__unset__")).toBe("");
    expect(flagsForCell("3")).toBe("");
  });
});

describe("taskCell", () => {
  it("is urgent when the due date is inside 48 hours", () => {
    expect(taskCell({ importance: 2, due_at: hours(47) }, NOW)).toBe("urgent");
    expect(taskCell({ importance: 2, due_at: hours(-5) }, NOW)).toBe("urgent");
    expect(taskCell({ importance: 2, leveraged: true, due_at: hours(1) }, NOW)).toBe(
      "critical"
    );
  });

  it("is not urgent when the due date is outside 48 hours, or absent", () => {
    expect(taskCell({ importance: 2, due_at: hours(49) }, NOW)).toBe("important");
    expect(taskCell({ importance: 2 }, NOW)).toBe("important");
    expect(taskCell({ leveraged: true, due_at: hours(72) }, NOW)).toBe("speculative-bet");
  });

  it("folds an urgent task that is not important into Low Priority", () => {
    expect(taskCell({ importance: 0, due_at: hours(1) }, NOW)).toBe("low-priority");
    expect(taskCell({ importance: null }, NOW)).toBe("low-priority");
  });

  it("agrees with the My Tasks formula for every input", () => {
    for (const important of [false, true]) {
      for (const leveraged of [false, true]) {
        for (const urgent of [false, true]) {
          const task = {
            importance: important ? 2 : 0,
            leveraged,
            due_at: urgent ? hours(4) : hours(24 * 10),
          };
          expect(taskCell(task, NOW)).toBe(cellForInputs({ important, leveraged, urgent }));
        }
      }
    }
  });
});
