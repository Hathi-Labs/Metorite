/**
 * The My Tasks prioritisation engine.
 *
 * ⚠️ This file did not exist until 2026-09-23. `priority.ts` calls itself
 * "pure + unit-testable; imported everywhere the UI slices by priority", and
 * nothing tested it. The org-priority seed changes `priorityInputs`, the one
 * function every surface reads, so the formula it feeds is pinned here first.
 */

import { describe, expect, it } from "vitest";

import {
  ORG_PRIORITY_SEED,
  actionMode,
  cellForInputs,
  isUntagged,
  isUrgent,
  modeSuggestion,
  priorityCell,
  priorityInputs,
  seededImportant,
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

describe("the org priority seeds a suggestion, never a decision", () => {
  it("seeds from High upward", () => {
    // Owner decision 2026-09-23: 2 High and 3 Highest.
    expect(ORG_PRIORITY_SEED).toBe(2);
    expect(seededImportant({ important: undefined, orgPriority: 3 })).toBe(true);
    expect(seededImportant({ important: undefined, orgPriority: 2 })).toBe(true);
    expect(seededImportant({ important: undefined, orgPriority: 1 })).toBe(false);
    expect(seededImportant({ important: undefined, orgPriority: 0 })).toBe(false);
    expect(seededImportant({ important: undefined, orgPriority: undefined })).toBe(false);
  });

  it("only while the member has said nothing", () => {
    // 🔴 The rule migration 188 was written to protect: the member's own
    // judgement is theirs. Any stated answer ends the seed, including "no".
    expect(seededImportant({ important: true, orgPriority: 3 })).toBe(false);
    expect(seededImportant({ important: false, orgPriority: 3 })).toBe(false);
  });

  it("lets an explicit 'not important' beat a Highest priority", () => {
    // The OUTCOME a member sees. It is two guards deep — `??` here, and
    // `seededImportant`'s "=== undefined" — so either one alone keeps it true.
    // Measured 2026-09-23: loosening the second to "falsy" fails the test
    // above, not this one. That test is the fence on the mechanism.
    const inputs = priorityInputs(
      { dueAt: undefined, important: false, leveraged: false, orgPriority: 3 },
      48,
      NOW,
    );
    expect(inputs.important).toBe(false);
  });

  it("moves an unjudged High task out of Low Priority", () => {
    // The whole point. Before the seed, a task a manager marked High landed
    // in the assignee's Low Priority for want of a flag nobody had set.
    const task = { dueAt: inHours(200), important: undefined, leveraged: false };
    expect(priorityCell({ ...task, orgPriority: undefined }, 48, NOW)).toBe("low-priority");
    expect(priorityCell({ ...task, orgPriority: 2 }, 48, NOW)).toBe("important");
  });

  it("still combines with the DERIVED urgency, from the shared due date", () => {
    // The org sets the due date; the due date sets urgency. So a High task
    // due tomorrow reaches the top half of the matrix with no one flagging it.
    const cell = priorityCell(
      { dueAt: inHours(20), important: undefined, leveraged: false, orgPriority: 2 },
      48,
      NOW,
    );
    expect(cell).toBe("urgent");
    expect(actionMode(
      { dueAt: inHours(20), important: undefined, leveraged: false, orgPriority: 2 },
      48,
      NOW,
    )).toBe("delegate");
  });

  it("leaves a seeded task UNTAGGED, so it still asks for the member's call", () => {
    // The seed is the org's guess. The triage prompt is what asks the member
    // for theirs, so it must keep showing until they answer.
    expect(isUntagged({ important: undefined, leveraged: undefined })).toBe(true);
  });

  it("feeds the delegate/schedule nudge the same way", () => {
    const item = {
      dueAt: inHours(200),
      important: undefined,
      leveraged: false,
      orgPriority: 3,
      disposition: "NEXT" as const,
      isMine: true,
      keptMine: false,
    };
    // Seeded important, not urgent → schedule, the "important" cell's mode.
    expect(modeSuggestion(item, 48, NOW)).toEqual({ mode: "schedule", cell: "important" });
  });
});
