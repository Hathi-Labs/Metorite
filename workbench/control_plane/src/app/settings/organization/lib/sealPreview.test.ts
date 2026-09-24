import { describe, expect, it } from "vitest";

import { describeHandover, describeSeal, type SealPreview } from "./sealPreview";

/**
 * D63's last requirement, as assertions.
 *
 * > **The deactivation dialog must state the split in numbers before the
 * > click** — *"14 tasks — 3 handed over, 11 sealed, not deleted; later access
 * > is recorded"*.
 *
 * The claim worth holding is not "a string comes back". It is that the string
 * says the three things D63 names — how many, what happens to them, and that
 * nothing is deleted — in every arm, including the two where a lazier
 * implementation would drop one.
 */

const preview = (over: Partial<SealPreview> = {}): SealPreview => ({
  projects: 3,
  tasks: 14,
  handed_over: 3,
  sealed: 11,
  ...over,
});

describe("the sentence D63 asks for", () => {
  it("states the split exactly as the decision words it", () => {
    const line = describeSeal(preview())!;
    expect(line).toContain("14 tasks");
    expect(line).toContain("3 handed over");
    expect(line).toContain("11 sealed");
  });

  it("always says nothing is deleted, and that access is recorded", () => {
    // ⚠️ Over EVERY arm, not just the happy one. D63 is emphatic that the tree
    // is "retained, invisible, NEVER deleted", and the moment of the click is
    // the only place that promise does any work. An arm that drops the clause
    // is the arm somebody reads on the day it matters.
    const arms = [
      preview(),
      preview({ handed_over: 0, sealed: 14 }),
      preview({ tasks: 0, handed_over: 0, sealed: 0 }),
      preview({ tasks: 1, handed_over: 0, sealed: 1 }),
      preview({ tasks: 1, handed_over: 1, sealed: 0 }),
    ];
    for (const p of arms) {
      const line = describeSeal(p);
      expect(line, JSON.stringify(p)).toContain("not deleted");
      expect(line, JSON.stringify(p)).toContain("later access is recorded");
    }
  });

  it("reads naturally when nothing was handed over — the COMMON case", () => {
    // Two guards refuse to create a task in a personal tree assigned to
    // somebody else, so 0 is the ordinary answer on a healthy tenant
    // (measured on production 2026-09-23: 0 of 2). It must not read as a
    // defect, and it must not print "0 handed over".
    const line = describeSeal(preview({ handed_over: 0, sealed: 14 }))!;
    expect(line).toContain("14 tasks");
    expect(line).toContain("all sealed");
    expect(line).not.toContain("0 handed over");
  });

  it("says nothing at all for a member with no personal tree", () => {
    // Otherwise the dialog warns about nothing, which teaches people to skip
    // the line on the day it is not nothing.
    expect(describeSeal(preview({ projects: 0, tasks: 0, handed_over: 0, sealed: 0 })))
      .toBeNull();
    expect(describeSeal(null)).toBeNull();
  });

  it("counts one task as a task, not as tasks", () => {
    expect(describeSeal(preview({ tasks: 1, handed_over: 0, sealed: 1 })))
      .toContain("1 task —");
  });

  it("falls back to projects when the tree holds no tasks", () => {
    const line = describeSeal(
      preview({ projects: 2, tasks: 0, handed_over: 0, sealed: 0 }),
    )!;
    expect(line).toContain("2 projects sealed");
  });
});

describe("who keeps what", () => {
  it("names the hand-over only when there is one", () => {
    expect(describeHandover(preview())).toContain("3 tasks stay with");
    expect(describeHandover(preview({ handed_over: 0 }))).toBeNull();
  });

  it("speaks of one colleague in the singular", () => {
    expect(describeHandover(preview({ handed_over: 1 })))
      .toBe("1 task stays with the colleague it is assigned to.");
  });
});
