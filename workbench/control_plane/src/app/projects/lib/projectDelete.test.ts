/**
 * The delete confirmation's rules (H-8).
 *
 * Every case here is a way the dialog could be wrong while still looking
 * right — an empty field that confirms, a sentence that reports "0 tasks" as
 * if that were a warning, a count sentence that reads as a complete inventory.
 * None of them is visible to a click test.
 */

import { describe, expect, it } from "vitest";

import type { NodeSummary } from "./api";
import { confirmsDeletion, deletionClauses, joinClauses } from "./projectDelete";

const summary = (over: Partial<NodeSummary> = {}): NodeSummary => ({
  id: "p1",
  name: "Roadmap",
  level: "project",
  tasks: 0,
  overdue: 0,
  by_category: {},
  projects: 0,
  children: [],
  ...over,
});

describe("confirmsDeletion", () => {
  it("accepts the name typed back", () => {
    expect(confirmsDeletion("Roadmap", "Roadmap")).toBe(true);
  });

  it("ignores case and surrounding space — the gate is reading, not typing", () => {
    expect(confirmsDeletion("  roadmap ", "Roadmap")).toBe(true);
    expect(confirmsDeletion("ROADMAP", "Roadmap")).toBe(true);
  });

  it("refuses a different name", () => {
    expect(confirmsDeletion("Roadmaps", "Roadmap")).toBe(false);
    expect(confirmsDeletion("Road map", "Roadmap")).toBe(false);
  });

  it("refuses an empty field", () => {
    expect(confirmsDeletion("", "Roadmap")).toBe(false);
    expect(confirmsDeletion("   ", "Roadmap")).toBe(false);
  });

  it("⚠️ never confirms against an empty NAME, however it is typed", () => {
    // The reachable version: the dialog rendered before its row loaded. An
    // untouched field would otherwise satisfy an untouched name and the
    // destructive button would arm itself.
    expect(confirmsDeletion("", "")).toBe(false);
    expect(confirmsDeletion("   ", "  ")).toBe(false);
    expect(confirmsDeletion("anything", "")).toBe(false);
  });
});

describe("deletionClauses", () => {
  it("counts subprojects and tasks", () => {
    expect(deletionClauses(summary({ projects: 3, tasks: 41 }))).toEqual([
      "3 subprojects",
      "41 tasks",
    ]);
  });

  it("says one of a thing in the singular", () => {
    expect(deletionClauses(summary({ projects: 1, tasks: 1 }))).toEqual([
      "1 subproject",
      "1 task",
    ]);
  });

  it("drops a zero rather than warning about nothing", () => {
    expect(deletionClauses(summary({ projects: 0, tasks: 12 }))).toEqual(["12 tasks"]);
    expect(deletionClauses(summary({ projects: 2, tasks: 0 }))).toEqual(["2 subprojects"]);
  });

  it("returns nothing at all for an empty project", () => {
    // The caller then says so in its own words. "0 subprojects and 0 tasks"
    // trains people to click through the dialog.
    expect(deletionClauses(summary())).toEqual([]);
  });

  it("returns nothing before the summary has loaded", () => {
    expect(deletionClauses(null)).toEqual([]);
  });
});

describe("joinClauses", () => {
  it("joins one, two and three clauses as English", () => {
    expect(joinClauses(["1 task"])).toBe("1 task");
    expect(joinClauses(["2 subprojects", "9 tasks"])).toBe("2 subprojects and 9 tasks");
    expect(joinClauses(["a", "b", "c"])).toBe("a, b and c");
  });

  it("is empty for no clauses", () => {
    expect(joinClauses([])).toBe("");
  });
});
