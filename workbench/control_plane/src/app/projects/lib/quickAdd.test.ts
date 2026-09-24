/**
 * Projects · group-context quick-add (WS-27y).
 *
 * The contract per axis: a task quick-added inside a group must come back IN
 * that group. Each case asserts the payload fragment says so — and that the
 * assignee axis routes through the follow-up PUT rather than pretending the
 * create body can carry people.
 */

import { describe, expect, it } from "vitest";

import { dayKey } from "./calendar";
import { UNSET } from "./grouping";
import {
  EMPTY_PLAN,
  dueInstantForDay,
  mergePlans,
  quickAddPrefill,
} from "./quickAdd";

describe("quickAddPrefill per axis", () => {
  it("status → the column's status_id", () => {
    expect(quickAddPrefill("status", "s-doing")).toEqual({
      create: { status_id: "s-doing" },
    });
  });

  it("project → creates IN the column's project", () => {
    expect(quickAddPrefill("project", "p-firmware")).toEqual({
      create: { project_id: "p-firmware" },
    });
  });

  it("importance → the flags the matrix level implies (D78)", () => {
    // The key is a level. The create body carries the integer and the
    // boolean, never the level string, which the gateway would refuse.
    expect(quickAddPrefill("importance", "critical")).toEqual({
      create: { importance: 2, leveraged: true },
    });
    expect(quickAddPrefill("importance", "urgent")).toEqual({
      create: { importance: 2, leveraged: false },
    });
    expect(quickAddPrefill("importance", "speculative-bet")).toEqual({
      create: { importance: 0, leveraged: true },
    });
    expect(quickAddPrefill("importance", "low-priority")).toEqual({
      create: { importance: 0, leveraged: false },
    });
  });

  it("tag → a one-element tags list", () => {
    expect(quickAddPrefill("tag", "bug")).toEqual({ create: { tags: ["bug"] } });
  });

  it("assignee → the follow-up PUT, never the create body", () => {
    // TaskIn has no assignees field; smuggling it into the POST would be
    // silently dropped and the add would land outside its column.
    const plan = quickAddPrefill("assignee", "priya@x.co");
    expect(plan.create).toEqual({});
    expect(plan.assignees).toEqual(["priya@x.co"]);
  });

  it("day → a due instant that renders on the clicked day", () => {
    const plan = quickAddPrefill("day", "2026-08-12");
    const due = plan.create.due_at as string;
    // Behavioural, not byte-for-byte: whatever instant is chosen, the
    // calendar (which reads due_at in the viewer's zone) must file it on the
    // day that was clicked.
    expect(dayKey(new Date(due))).toBe("2026-08-12");
  });

  it("keeps the instant mid-day, so nearby timezones agree on the date", () => {
    const due = new Date(dueInstantForDay("2026-08-12"));
    expect(due.getHours()).toBe(12);
  });

  it("asks for nothing in an UNSET bucket, on every axis", () => {
    // A task is born unassigned, untagged and priority-less — "create it in
    // the Unassigned column" is what a bare create already does.
    for (const axis of ["assignee", "tag", "importance"]) {
      expect(quickAddPrefill(axis, UNSET)).toEqual(EMPTY_PLAN);
    }
  });

  it("treats 'none' and unknown future axes as a plain add, never an error", () => {
    expect(quickAddPrefill("none", "all")).toEqual(EMPTY_PLAN);
    expect(quickAddPrefill("phase", "beta")).toEqual(EMPTY_PLAN);
  });
});

describe("mergePlans — a lane cell is two contexts at once", () => {
  it("merges a column's field with a lane's field", () => {
    const merged = mergePlans(
      quickAddPrefill("status", "s-doing"),
      quickAddPrefill("importance", "important")
    );
    expect(merged.create).toEqual({
      status_id: "s-doing",
      importance: 2,
      leveraged: false,
    });
    expect(merged.assignees).toBeUndefined();
  });

  it("carries the lane's assignee through the merge", () => {
    const merged = mergePlans(
      quickAddPrefill("status", "s-todo"),
      quickAddPrefill("assignee", "ravi@x.co")
    );
    expect(merged.create).toEqual({ status_id: "s-todo" });
    expect(merged.assignees).toEqual(["ravi@x.co"]);
  });

  it("unions assignees rather than letting the later plan overwrite", () => {
    const merged = mergePlans(
      { create: {}, assignees: ["a@x.co"] },
      { create: {}, assignees: ["a@x.co", "b@x.co"] }
    );
    expect(merged.assignees).toEqual(["a@x.co", "b@x.co"]);
  });

  it("merging nothing is still a valid plan", () => {
    expect(mergePlans()).toEqual({ create: {} });
  });
});
