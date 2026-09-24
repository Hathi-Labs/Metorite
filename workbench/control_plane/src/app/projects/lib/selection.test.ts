/**
 * Projects · multi-select and the bulk request (WS-27n).
 *
 * The cases a selection UI always gets wrong: a shift-click whose anchor has
 * scrolled out of the filtered set, a selection that outlives the filter that
 * produced it, a task drawn in two columns at once, and a "Low" priority whose
 * value is the falsy `0`.
 */

import { describe, expect, it } from "vitest";

import type { TaskRow } from "./api";
import { EMPTY_FILTERS } from "./grouping";
import {
  type BulkDraft,
  EMPTY_DRAFT,
  MAX_BULK,
  allSelected,
  buildRequest,
  describeOutcome,
  prune,
  range,
  selectionSurvives,
  toggle,
  visibleIds,
} from "./selection";

const task = (id: string): TaskRow => ({
  id,
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s1",
  title: id,
});

const draft = (over: Partial<BulkDraft> = {}): BulkDraft => ({
  ...EMPTY_DRAFT,
  ...over,
});

describe("toggle", () => {
  it("adds what is not there and removes what is", () => {
    expect([...toggle(new Set(), "a")]).toEqual(["a"]);
    expect([...toggle(new Set(["a"]), "a")]).toEqual([]);
  });

  it("does not mutate the set it was given", () => {
    const current = new Set(["a"]);
    toggle(current, "b");
    expect([...current]).toEqual(["a"]);
  });
});

describe("range", () => {
  const visible = ["a", "b", "c", "d"];

  it("selects everything between the two, inclusive", () => {
    expect(range(visible, "b", "d")).toEqual(["b", "c", "d"]);
  });

  it("works when the shift-click went upwards", () => {
    expect(range(visible, "d", "b")).toEqual(["b", "c", "d"]);
  });

  it("is one task when both ends are the same card", () => {
    expect(range(visible, "b", "b")).toEqual(["b"]);
  });

  it("falls back to just the target when the anchor has been filtered away", () => {
    // Otherwise a stale anchor selects a range that means nothing on the
    // board now in front of somebody.
    expect(range(visible, "gone", "c")).toEqual(["c"]);
  });

  it("uses the ORDER ON SCREEN, not the order ids were selected in", () => {
    // "Between these two" means between them as drawn, after the filter and
    // the grouping have decided what is drawn at all.
    expect(range(["d", "c", "b", "a"], "d", "b")).toEqual(["d", "c", "b"]);
  });
});

describe("prune", () => {
  it("drops ids that are no longer on the page", () => {
    // A selection that outlives its filter is how a bulk edit hits tasks
    // nobody can see any more.
    expect([...prune(new Set(["a", "b"]), ["b"])]).toEqual(["b"]);
  });

  it("keeps everything when nothing was filtered away", () => {
    expect([...prune(new Set(["a", "b"]), ["a", "b"])].sort()).toEqual(["a", "b"]);
  });

  it("empties the selection when the page does", () => {
    expect([...prune(new Set(["a"]), [])]).toEqual([]);
  });
});

describe("visibleIds", () => {
  it("counts a task drawn in two columns ONCE", () => {
    // A task with two assignees appears in both their columns (WS-27k), so
    // the same id genuinely appears twice in the groups.
    const groups = [{ tasks: [task("a"), task("b")] }, { tasks: [task("a")] }];
    expect(visibleIds(groups)).toEqual(["a", "b"]);
  });

  it("keeps the board's own order", () => {
    const groups = [{ tasks: [task("z")] }, { tasks: [task("a")] }];
    expect(visibleIds(groups)).toEqual(["z", "a"]);
  });
});

describe("allSelected", () => {
  it("is false for an empty board, so the box is not ticked over nothing", () => {
    expect(allSelected(new Set(), [])).toBe(false);
  });

  it("is true only when every visible task is in the selection", () => {
    expect(allSelected(new Set(["a"]), ["a", "b"])).toBe(false);
    expect(allSelected(new Set(["a", "b"]), ["a", "b"])).toBe(true);
  });

  it("ignores selected ids that are no longer visible", () => {
    expect(allSelected(new Set(["a", "gone"]), ["a"])).toBe(true);
  });
});

describe("buildRequest", () => {
  it("is null when nothing is selected", () => {
    expect(buildRequest([], draft({ status: "Done" }))).toBeNull();
  });

  it("is null when the draft asks for nothing", () => {
    // The gateway answers 422 for a no-op; a button that fires only to be told
    // "nothing to change" should have been disabled instead.
    expect(buildRequest(["a"], EMPTY_DRAFT)).toBeNull();
  });

  it("sends the status by NAME, never by id", () => {
    // A selection can span projects and a status id belongs to one root, so an
    // id would put most of the selection in a lane that is not theirs.
    const request = buildRequest(["a"], draft({ status: "Done" }));
    expect(request?.patch).toEqual({ status: "Done" });
    expect(JSON.stringify(request)).not.toContain("status_id");
  });

  it("sets both priority flags from one choice (D78)", () => {
    expect(buildRequest(["a"], draft({ priority: "important" }))?.patch).toEqual({
      importance: 2,
      leveraged: false,
    });
    expect(buildRequest(["a"], draft({ priority: "leveraged" }))?.patch).toEqual({
      importance: 0,
      leveraged: true,
    });
    expect(buildRequest(["a"], draft({ priority: "both" }))?.patch).toEqual({
      importance: 2,
      leveraged: true,
    });
  });

  it("clears both flags on 'none', which is a real choice and not 'unset'", () => {
    // The trap: 'none' and the untouched box must not read the same.
    expect(buildRequest(["a"], draft({ priority: "none" }))?.patch).toEqual({
      importance: 0,
      leveraged: false,
    });
  });

  it("leaves priority alone when the box was untouched", () => {
    expect(buildRequest(["a"], draft({ status: "Done", priority: "" }))?.patch)
      .toEqual({ status: "Done" });
  });

  it("splits a comma-separated list of people and trims it", () => {
    expect(
      buildRequest(["a"], draft({ assigneeAdd: " priya@x.co , ravi@x.co " }))
        ?.assignees_add
    ).toEqual(["priya@x.co", "ravi@x.co"]);
  });

  it("offers add and remove, and never a replace", () => {
    // A replace across a selection wipes every individual assignment the
    // selected tasks already carried.
    const request = buildRequest(
      ["a"],
      draft({ assigneeAdd: "a@x.co", assigneeRemove: "b@x.co" })
    );
    expect(request?.assignees_add).toEqual(["a@x.co"]);
    expect(request?.assignees_remove).toEqual(["b@x.co"]);
    expect(JSON.stringify(request)).not.toContain("assignees_set");
  });

  it("omits the keys it has nothing for, rather than sending empty lists", () => {
    const request = buildRequest(["a"], draft({ tagAdd: "bug" }));
    expect(request).toEqual({ task_ids: ["a"], tags_add: ["bug"] });
  });

  it("copies the selection rather than aliasing it", () => {
    const ids = ["a"];
    const request = buildRequest(ids, draft({ status: "Done" }));
    expect(request?.task_ids).not.toBe(ids);
  });

  it("mirrors the gateway's cap", () => {
    expect(MAX_BULK).toBe(500);
  });
});

describe("describeOutcome", () => {
  const base = { requested: 3, applied: 3, skipped: [], failed: [] };

  it("says how many of how many", () => {
    expect(describeOutcome(base)).toBe("3 of 3 updated.");
  });

  it("names the ones that were already like that", () => {
    // "47 changed" against "I selected 50" is a support conversation; this is
    // a sentence somebody already read.
    expect(
      describeOutcome({
        ...base,
        applied: 2,
        skipped: [{ task_id: "c", reason: "unchanged" }],
      })
    ).toBe("2 of 3 updated, 1 already like that.");
  });

  it("does not say WHY a task was unavailable", () => {
    // The API answers 404 for "not yours" as well as "no such thing" (R5), and
    // the UI must not invent a distinction the server refuses to make.
    const said = describeOutcome({
      ...base,
      applied: 2,
      skipped: [{ task_id: "c", reason: "not_found" }],
    });
    expect(said).toContain("1 not available");
    expect(said).not.toMatch(/permission|denied|forbidden|deleted/i);
  });

  it("reports failures", () => {
    expect(
      describeOutcome({
        ...base,
        applied: 2,
        failed: [{ task_id: "c", reason: "no status 'Done' in this project" }],
      })
    ).toContain("1 failed");
  });

  it("reports every category at once", () => {
    const said = describeOutcome({
      requested: 4,
      applied: 1,
      skipped: [
        { task_id: "b", reason: "unchanged" },
        { task_id: "c", reason: "not_found" },
      ],
      failed: [{ task_id: "d", reason: "nope" }],
    });
    expect(said).toBe(
      "1 of 4 updated, 1 already like that, 1 not available, 1 failed."
    );
  });
});

describe("selectionSurvives", () => {
  it("is true when the filters did not move", () => {
    expect(selectionSurvives(EMPTY_FILTERS, { ...EMPTY_FILTERS })).toBe(true);
  });

  it("is false the moment a filter changes", () => {
    expect(
      selectionSurvives(EMPTY_FILTERS, { ...EMPTY_FILTERS, overdue: true })
    ).toBe(false);
  });
});
