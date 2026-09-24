/**
 * /tasks · group-context quick-add prefill.
 *
 * What is pinned: every settable axis maps to the field that files the task
 * into that group; every unset bucket maps to a bare create (which already
 * lands there); and the computed axes refuse (`null`) rather than offering an
 * add that would visibly land in a sibling group.
 */

import { describe, expect, it } from "vitest";

import { NO_CONTEXT_GROUP } from "./priority";
import { quickAddPrefill, viewQuickAdd } from "./quickAdd";

describe("quickAddPrefill", () => {
  it("files a status-axis add into its category — board column or list section (D73.9)", () => {
    expect(quickAddPrefill("", "in_progress")).toEqual({ statusCategory: "in_progress" });
    expect(quickAddPrefill("", "done")).toEqual({ statusCategory: "done" });
    // A lane NAME is not a group key any more. It files a bare create.
    expect(quickAddPrefill("", "IN PROCESS")).toEqual({});
  });

  it("files a context-group add under that @context", () => {
    expect(quickAddPrefill("context", "@computer")).toEqual({ context: "@computer" });
  });

  it("lets the no-context bucket stay a bare create — a bare item already lands there", () => {
    expect(quickAddPrefill("context", NO_CONTEXT_GROUP)).toEqual({});
  });

  it("files an energy-group add at that energy, and the unset bucket bare", () => {
    expect(quickAddPrefill("energy", "high")).toEqual({ energy: "high" });
    expect(quickAddPrefill("energy", "none")).toEqual({});
  });

  it("marks a deep-work-group add deep, and shallow bare", () => {
    expect(quickAddPrefill("depth", "deep")).toEqual({ deepWork: true });
    expect(quickAddPrefill("depth", "shallow")).toEqual({});
  });

  it("treats the flat list as a plain add", () => {
    expect(quickAddPrefill("none", "all")).toEqual({});
  });

  it("refuses the computed axes — no payload can promise the landing", () => {
    expect(quickAddPrefill("priority", "critical")).toBeNull();
    expect(quickAddPrefill("mode", "do")).toBeNull();
  });
});

describe("viewQuickAdd — the flat views, whose group IS the view (WS-27ad)", () => {
  it("files a Someday add into Someday", () => {
    // The clean case: "incubate this" is a whole decision, and the box saves
    // the capture-then-clarify round trip that was the only way to say it.
    expect(viewQuickAdd("someday")?.prefill).toEqual({ disposition: "SOMEDAY" });
  });

  it("logs a Done add as already done", () => {
    expect(viewQuickAdd("done")?.prefill).toEqual({ disposition: "DONE" });
  });

  it("refuses Waiting-For — a create cannot promise the delegation", () => {
    // The view groups by PERSON. A create can set the bucket but not who owes
    // it, so the task would visibly file itself under "Unassigned" — a sibling
    // group, i.e. the same lie the computed axes refuse above.
    expect(viewQuickAdd("waiting")).toBeNull();
  });

  it("refuses Archive — creating a task to immediately hide it is not a gesture", () => {
    expect(viewQuickAdd("archive")).toBeNull();
  });

  it("refuses the views that already have their own capture affordance", () => {
    // Inbox has the always-open capture row; next/engage/priority are grouped
    // surfaces served by `quickAddPrefill`; calendar days get a per-day box.
    for (const view of ["inbox", "next", "engage", "priority", "calendar"] as const) {
      expect(viewQuickAdd(view), view).toBeNull();
    }
  });

  it("labels every box it offers", () => {
    // A capture box whose placeholder does not say where the task lands is the
    // failure the group-context quick-add exists to avoid.
    for (const view of ["someday", "done"] as const) {
      expect(viewQuickAdd(view)?.label, view).toBeTruthy();
    }
  });
});
