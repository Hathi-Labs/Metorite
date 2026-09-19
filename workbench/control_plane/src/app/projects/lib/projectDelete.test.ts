/**
 * The delete confirmation's rules (H-8).
 *
 * Every case here is a way the dialog could be wrong while still looking
 * right — an empty field that confirms, a sentence that reports "0 tasks" as
 * if that were a warning, a count sentence that reads as a complete inventory.
 * None of them is visible to a click test.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import type { NodeSummary } from "./api";
import {
  COUNT_CAVEAT,
  confirmsDeletion,
  deletionClauses,
  joinClauses,
} from "./projectDelete";

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

describe("🔴 the dialog may never claim a node is empty", () => {
  // The rule this module exists for, and the one the first version broke.
  //
  // `node_counts_sql` filters `t.archived_at IS NULL`; `delete_node` counts
  // `pm_tasks` with no such filter. A project under a lifecycle policy can
  // therefore summarise as 0 tasks and delete 500 of them. A sentence that
  // reads "holds no subprojects and no tasks" is a positive claim of
  // emptiness the server contradicts a second later.
  //
  // A source scan, because no behavioural assertion can tell a correct
  // sentence from a plausible one — the defect was ENGLISH, not logic.
  const source = readFileSync(
    new URL("../components/DeleteProjectDialog.tsx", import.meta.url),
    "utf-8",
  );
  /**
   * Comments stripped before the scan.
   *
   * The dialog's own comment QUOTES the sentence this rule forbids, because
   * explaining a defect needs to name it. Scanning the raw file made the
   * fence fire on its own rationale — correct behaviour, wrong scope. What
   * the rule is about is what a member READS.
   */
  const dialog = source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ");

  it("states the removal unconditionally, with no emptiness branch", () => {
    expect(dialog).not.toMatch(/holds no/i);
    expect(dialog).not.toMatch(/\bis empty\b/i);
    expect(dialog).not.toMatch(/nothing (under|inside|in) it/i);
  });

  it("carries the caveat wherever it shows a count", () => {
    // Exported as a constant precisely so a second surface cannot show the
    // counts without it.
    expect(dialog).toContain("COUNT_CAVEAT");
    expect(COUNT_CAVEAT).toMatch(/archived/i);
    expect(COUNT_CAVEAT).toMatch(/folder/i);
  });

  it("names the counts as a floor, not a total", () => {
    expect(dialog).toMatch(/At least/);
  });
});
