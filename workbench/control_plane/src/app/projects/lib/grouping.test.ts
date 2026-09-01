/**
 * Projects · grouping and the filter query (WS-27k).
 *
 * The cases that decide whether a board is trustworthy are the awkward ones: a
 * task with two owners, a task with none, a lane with nothing in it, and a
 * saved view written by a client that no longer exists.
 */

import { describe, expect, it } from "vitest";

import type { StatusRow, TaskRow } from "./api";
import {
  type BoardLanes,
  DEFAULT_GROUP_BY,
  EMPTY_FILTERS,
  GROUP_OPTIONS,
  NO_LANES,
  UNSET,
  fromConfig,
  groupTasks,
  isFiltered,
  personLabel,
  type ViewState,
  describeDivergence,
  toConfig,
  toQuery,
  viewDivergence,
} from "./grouping";
import { DEFAULT_SHOWN } from "./shownFields";

const status = (id: string, name: string, position: number): StatusRow => ({
  id,
  project_id: "p1",
  name,
  color: "#888888",
  position,
  category: "todo",
  is_default: false,
});

const task = (over: Partial<TaskRow> = {}): TaskRow => ({
  id: "t1",
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s-todo",
  title: "Fix the extruder",
  ...over,
});

const STATUSES = [status("s-doing", "In progress", 2), status("s-todo", "To do", 1)];
const ctx = { statuses: STATUSES };

describe("groupTasks by status", () => {
  it("orders lanes by position, not by insertion", () => {
    expect(groupTasks([], "status", ctx).map((g) => g.label)).toEqual([
      "To do",
      "In progress",
    ]);
  });

  it("keeps an empty lane", () => {
    // A board missing its "In progress" column reads as "this project has no
    // in-progress state", not "nothing is in progress".
    const groups = groupTasks([task()], "status", ctx);
    expect(groups.map((g) => g.tasks.length)).toEqual([1, 0]);
  });
});

describe("groupTasks by assignee", () => {
  it("puts a task with two owners in both columns", () => {
    // It IS both people's work. Picking one arbitrarily hides it from the other.
    const groups = groupTasks(
      [task({ assignees: ["priya@x.co", "ravi@x.co"] })],
      "assignee",
      ctx,
    );
    expect(groups.map((g) => g.label)).toEqual(["priya", "ravi"]);
    expect(groups.every((g) => g.tasks.length === 1)).toBe(true);
  });

  it("collects tasks with nobody on them under Unassigned", () => {
    const groups = groupTasks(
      [task({ id: "a", assignees: [] }), task({ id: "b" })],
      "assignee",
      ctx,
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe(UNSET);
    expect(groups[0].tasks).toHaveLength(2);
  });

  it("puts Unassigned last, because it is a residue not a peer", () => {
    const groups = groupTasks(
      [task({ id: "a" }), task({ id: "b", assignees: ["zoe@x.co"] })],
      "assignee",
      ctx,
    );
    expect(groups.map((g) => g.key)).toEqual(["zoe@x.co", UNSET]);
  });

  it("drops empty buckets — there is no 'people with nothing assigned' column", () => {
    expect(groupTasks([], "assignee", ctx)).toEqual([]);
  });

  it("labels an agent by its handle, not as 'agent'", () => {
    expect(personLabel("agent:builder")).toBe("builder");
    expect(personLabel("priya@fracktal.in")).toBe("priya");
  });
});

describe("groupTasks by project and importance", () => {
  it("names projects through the supplied lookup", () => {
    const groups = groupTasks([task({ project_id: "p2" })], "project", {
      statuses: STATUSES,
      projectName: (id) => (id === "p2" ? "Firmware" : "?"),
    });
    expect(groups[0].label).toBe("Firmware");
  });

  it("falls back to a word when the project is not in the tree", () => {
    // The tree is filtered by Center; a task can belong to a project the
    // sidebar is not currently showing. A blank heading reads as a bug.
    expect(groupTasks([task()], "project", ctx)[0].label).toBe("Project");
  });

  it("separates priorities and keeps the unset ones last", () => {
    const groups = groupTasks(
      [task({ id: "a", importance: 3 }), task({ id: "b" })],
      "importance",
      ctx,
    );
    expect(groups.map((g) => g.label)).toEqual(["Urgent", "No priority"]);
  });

  it("treats importance 0 as a real value, not as absent", () => {
    // `0` is falsy — the bug this guards is a Low-priority task silently
    // landing in "No priority".
    const groups = groupTasks([task({ importance: 0 })], "importance", ctx);
    expect(groups[0].key).toBe("0");
    expect(groups[0].label).toBe("Low");
  });
});

describe("groupTasks none", () => {
  it("is one group carrying the count", () => {
    const groups = groupTasks([task(), task({ id: "t2" })], "none", ctx);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("2 tasks");
  });
});

describe("toQuery", () => {
  it("sends nothing when nothing is filtered", () => {
    expect(toQuery(EMPTY_FILTERS)).toEqual({});
    expect(isFiltered(EMPTY_FILTERS)).toBe(false);
  });

  it("omits a false toggle rather than sending false", () => {
    // `?overdue=false` is indistinguishable from "the client forgot to send it".
    expect(toQuery({ ...EMPTY_FILTERS, overdue: false })).toEqual({});
    expect(toQuery({ ...EMPTY_FILTERS, overdue: true })).toEqual({ overdue: "true" });
  });

  it("does not send a whitespace-only search", () => {
    expect(toQuery({ ...EMPTY_FILTERS, q: "   " })).toEqual({});
  });

  it("trims what it does send", () => {
    expect(toQuery({ ...EMPTY_FILTERS, q: "  jam " })).toEqual({ q: "jam" });
  });
});

describe("saved view config", () => {
  it("round-trips", () => {
    const filters = { ...EMPTY_FILTERS, overdue: true, statusCategory: "todo" };
    expect(fromConfig(toConfig(filters, "assignee"))).toEqual({
      filters,
      groupBy: "assignee",
      lanes: NO_LANES,
      shownFields: [...DEFAULT_SHOWN],
    });
  });

  it("stores a toggle as a boolean, not as the string a URL would carry", () => {
    // `toQuery` writes "true" because a query string has only text. A config is
    // JSON, and `fromConfig` refuses a string where a toggle belongs — so a
    // view built from query shape would come back with its toggles cleared.
    const config = toConfig({ ...EMPTY_FILTERS, overdue: true }, "status");
    expect((config.filters as Record<string, unknown>).overdue).toBe(true);
  });

  it("stores only what is set, so an unfiltered view is an empty object", () => {
    expect(toConfig(EMPTY_FILTERS, "status")).toEqual({
      filters: {},
      group_by: "status",
    });
  });

  it("uses the gateway's key names, not the form's", () => {
    // `normalise_view_config` drops keys it does not know, so `statusCategory`
    // would be silently discarded and the saved view would show everything.
    const config = toConfig({ ...EMPTY_FILTERS, statusCategory: "todo" }, "status");
    expect(config.filters).toEqual({ status_category: "todo" });
  });

  it("survives a config from a client that no longer exists", () => {
    // A saved view is a preference, not a contract. Refusing to open one
    // because it carries an unknown shape would strand people's views.
    for (const junk of [null, undefined, [], "board", 7, { filters: "nope" }]) {
      const got = fromConfig(junk);
      expect(got.filters).toEqual(EMPTY_FILTERS);
      expect(got.groupBy).toBe("status");
    }
  });

  it("falls back to the board's own axis for an unknown grouping", () => {
    expect(fromConfig({ group_by: "phase" }).groupBy).toBe("status");
  });

  it("ignores a non-boolean where a toggle belongs", () => {
    // `"true"` from a hand-edited config must not read as true — a string is
    // not a decision somebody made in the UI.
    expect(fromConfig({ filters: { overdue: "true" } }).filters.overdue).toBe(false);
  });

  it("accepts every grouping the board advertises", () => {
    for (const option of GROUP_OPTIONS) {
      expect(fromConfig({ group_by: option }).groupBy).toBe(option);
    }
  });
});

describe("swimlane state in a saved view (WS-27y)", () => {
  const lanes: BoardLanes = {
    subGroupBy: "assignee",
    collapsedLanes: ["zoe@x.co", UNSET],
    showEmptyLanes: true,
  };

  it("round-trips the sub-axis, the folded lanes and the empty-lane toggle", () => {
    expect(fromConfig(toConfig(EMPTY_FILTERS, "status", lanes))).toEqual({
      filters: EMPTY_FILTERS,
      groupBy: "status",
      lanes,
      shownFields: [...DEFAULT_SHOWN],
    });
  });

  it("stores nothing for a flat board, so lane-less views stay byte-identical", () => {
    expect(toConfig(EMPTY_FILTERS, "status", NO_LANES)).toEqual(
      toConfig(EMPTY_FILTERS, "status")
    );
    expect(toConfig(EMPTY_FILTERS, "status")).toEqual({
      filters: {},
      group_by: "status",
    });
  });

  it("keeps collapsed lanes a JSON array, not a CSV", () => {
    // Lane keys are addresses and sentinels; an address containing a comma is
    // unlikely, but the config is JSON and a list should stay a list.
    const config = toConfig(EMPTY_FILTERS, "status", lanes);
    expect(config.collapsed_lanes).toEqual(["zoe@x.co", UNSET]);
  });

  it("normalises a sub-axis equal to the main axis to none", () => {
    // A board laned by its own columns is nonsense a hand-edited config could
    // still say; every consumer sees the normalised truth.
    const got = fromConfig({ group_by: "status", sub_group_by: "status" });
    expect(got.lanes.subGroupBy).toBe("none");
    // ...and toConfig refuses to write it in the first place.
    expect(
      toConfig(EMPTY_FILTERS, "status", { ...lanes, subGroupBy: "status" })
    ).not.toHaveProperty("sub_group_by");
  });

  it("drops junk lane state from a hand-edited config", () => {
    const got = fromConfig({
      sub_group_by: "phase",
      collapsed_lanes: [7, null, "real"],
      show_empty_lanes: "true",
    });
    expect(got.lanes.subGroupBy).toBe("none");
    expect(got.lanes.collapsedLanes).toEqual(["real"]);
    // A string is not a decision somebody made in the UI (same rule as
    // overdue).
    expect(got.lanes.showEmptyLanes).toBe(false);
  });

  it("reads an old config with no lane keys as a flat board", () => {
    expect(fromConfig({ filters: {}, group_by: "status" }).lanes).toEqual(NO_LANES);
  });

  it("does not persist collapse state without its axis", () => {
    const config = toConfig(EMPTY_FILTERS, "status", {
      ...NO_LANES,
      collapsedLanes: ["ghost"],
    });
    // A collapsed-lane list without the axis it belonged to is keys from a
    // board that no longer exists.
    expect(config).not.toHaveProperty("collapsed_lanes");
  });
});

describe("shown fields in a saved view (WS-27x)", () => {
  it("round-trips a non-default set", () => {
    const shown = ["status", "tags", "custom.budget"];
    expect(fromConfig(toConfig(EMPTY_FILTERS, "status", NO_LANES, shown))).toEqual({
      filters: EMPTY_FILTERS,
      groupBy: "status",
      lanes: NO_LANES,
      shownFields: shown,
    });
  });

  it("stores nothing for the default set, so untouched views stay byte-identical", () => {
    // Same rule as lane state: a view saved before shown-fields existed and
    // one saved after with untouched columns must be the same bytes.
    expect(toConfig(EMPTY_FILTERS, "status", NO_LANES, [...DEFAULT_SHOWN])).toEqual(
      toConfig(EMPTY_FILTERS, "status")
    );
  });

  it("compares against the default as a SET, not a sequence", () => {
    // Toggling a field off and back on reorders the list; that is not a
    // change somebody made to the view.
    const reordered = [...DEFAULT_SHOWN].reverse();
    expect(toConfig(EMPTY_FILTERS, "status", NO_LANES, reordered)).not.toHaveProperty(
      "shown_fields"
    );
  });

  it("stores an explicitly emptied set — hiding everything is a choice", () => {
    const config = toConfig(EMPTY_FILTERS, "status", NO_LANES, []);
    expect(config.shown_fields).toEqual([]);
    expect(fromConfig(config).shownFields).toEqual([]);
  });

  it("reads an old config with no shown_fields as the default set", () => {
    expect(fromConfig({ filters: {}, group_by: "status" }).shownFields).toEqual([
      ...DEFAULT_SHOWN,
    ]);
  });

  it("drops junk keys from a hand-edited config", () => {
    // Same discipline the server applies (`normalise_view_config`): unknown
    // and non-string keys dropped, duplicates collapsed, `custom.` alone
    // names nothing.
    expect(
      fromConfig({
        shown_fields: ["status", "phase", 7, "custom.", "custom.budget", "status"],
      }).shownFields
    ).toEqual(["status", "custom.budget"]);
  });

  it("reads a non-list shown_fields as absent, never as hidden-everything", () => {
    expect(fromConfig({ shown_fields: "status,tags" }).shownFields).toEqual([
      ...DEFAULT_SHOWN,
    ]);
  });
});

describe("groupTasks by tag (WS-27m)", () => {
  it("puts a task with three tags in all three columns", () => {
    // Same reason as two assignees: it genuinely belongs to each, and picking
    // one hides it from the others.
    const groups = groupTasks([task({ tags: ["bug", "ops", "urgent"] })], "tag", ctx);
    expect(groups.map((g) => g.label)).toEqual(["bug", "ops", "urgent"]);
    expect(groups.every((g) => g.tasks.length === 1)).toBe(true);
  });

  it("collects untagged work under one bucket, last", () => {
    const groups = groupTasks(
      [task({ id: "a", tags: [] }), task({ id: "b", tags: ["bug"] }), task({ id: "c" })],
      "tag",
      ctx,
    );
    expect(groups.map((g) => g.key)).toEqual(["bug", UNSET]);
    expect(groups[1].tasks.map((t) => t.id)).toEqual(["a", "c"]);
  });

  it("shows a tag's own spelling, not a prettified one", () => {
    // The registry already decided the canonical spelling; re-casing it here
    // would show something no filter matches.
    expect(groupTasks([task({ tags: ["needs review"] })], "tag", ctx)[0].label).toBe(
      "needs review",
    );
  });

  it("drops nothing into an empty board", () => {
    expect(groupTasks([], "tag", ctx)).toEqual([]);
  });
});

describe("tag filters in the query", () => {
  it("sends tags as CSV, matching the gateway's split_csv", () => {
    expect(toQuery({ ...EMPTY_FILTERS, tags: ["bug", "ops"] })).toEqual({
      tags: "bug,ops",
    });
  });

  it("sends nothing for an empty tag list", () => {
    expect(toQuery({ ...EMPTY_FILTERS, tags: [] })).toEqual({});
  });

  it("counts as filtered", () => {
    expect(isFiltered({ ...EMPTY_FILTERS, tags: ["bug"] })).toBe(true);
  });

  it("round-trips through a saved view", () => {
    const filters = { ...EMPTY_FILTERS, tags: ["bug", "ops"] };
    expect(fromConfig(toConfig(filters, "tag"))).toEqual({
      filters,
      groupBy: "tag",
      lanes: NO_LANES,
      shownFields: [...DEFAULT_SHOWN],
    });
  });

  it("survives a config that stored tags as an array instead of CSV", () => {
    // An older or hand-written config. Reading it as a string and getting
    // garbage would be worse than reading it as unset.
    expect(fromConfig({ filters: { tags: ["bug"] } }).filters.tags).toEqual([]);
  });
});

describe("divergence — when the board stopped being the view (WS-27ab)", () => {
  const live = (over: Partial<ViewState> = {}): ViewState => ({
    filters: EMPTY_FILTERS,
    groupBy: "status",
    lanes: NO_LANES,
    shownFields: [...DEFAULT_SHOWN],
    ...over,
  });

  it("a state saved and read straight back is clean", () => {
    const state = live({
      filters: { ...EMPTY_FILTERS, q: "parser", overdue: true, tags: ["bug"] },
      groupBy: "assignee",
      lanes: { subGroupBy: "status", collapsedLanes: ["s1"], showEmptyLanes: true },
      shownFields: ["status", "due_at"],
    });
    const saved = toConfig(
      state.filters,
      state.groupBy,
      state.lanes,
      state.shownFields,
    );
    expect(viewDivergence(state, saved)).toEqual({ dirty: false, changed: [] });
  });

  it("the default board against an empty config is clean", () => {
    expect(viewDivergence(live(), {})).toEqual({ dirty: false, changed: [] });
    expect(viewDivergence(live(), null)).toEqual({ dirty: false, changed: [] });
  });

  it("names the part that moved, and only that part", () => {
    const saved = toConfig(EMPTY_FILTERS, "status", NO_LANES, DEFAULT_SHOWN);
    expect(
      viewDivergence(live({ filters: { ...EMPTY_FILTERS, overdue: true } }), saved),
    ).toEqual({ dirty: true, changed: ["filters"] });
    expect(viewDivergence(live({ groupBy: "assignee" }), saved)).toEqual({
      dirty: true,
      changed: ["grouping"],
    });
    // The sub-axis is part of the grouping, not of the lane state: turning
    // swimlanes on changes what the board IS.
    expect(
      viewDivergence(live({ lanes: { ...NO_LANES, subGroupBy: "assignee" } }), saved),
    ).toEqual({ dirty: true, changed: ["grouping"] });
    // Folding a lane on an axis that was already saved is lane state alone.
    expect(
      viewDivergence(
        live({
          lanes: { subGroupBy: "assignee", collapsedLanes: ["x"], showEmptyLanes: false },
        }),
        toConfig(EMPTY_FILTERS, "status", {
          subGroupBy: "assignee",
          collapsedLanes: [],
          showEmptyLanes: false,
        }),
      ),
    ).toEqual({ dirty: true, changed: ["lanes"] });
    expect(viewDivergence(live({ shownFields: ["status"] }), saved)).toEqual({
      dirty: true,
      changed: ["fields"],
    });
  });

  it("reports several parts at once, in a stable order", () => {
    const saved = toConfig(EMPTY_FILTERS, "status", NO_LANES, DEFAULT_SHOWN);
    const { changed } = viewDivergence(
      live({
        filters: { ...EMPTY_FILTERS, q: "x" },
        groupBy: "tag",
        shownFields: ["tags"],
      }),
      saved,
    );
    expect(changed).toEqual(["filters", "grouping", "fields"]);
    expect(describeDivergence(changed)).toBe("filters, grouping and shown fields");
  });

  it("reads a saved config by MEANING, not by its bytes", () => {
    // Each of these is a config `fromConfig` normalises. A byte comparison
    // would call an untouched board dirty on every one of them.
    const state = live();
    // A sub-axis equal to the main axis is normalised to "none".
    expect(
      viewDivergence(state, { group_by: "status", sub_group_by: "status" }).dirty,
    ).toBe(false);
    // A field key no vocabulary knows is dropped.
    expect(
      viewDivergence(state, { shown_fields: [...DEFAULT_SHOWN, "moon_phase"] }).dirty,
    ).toBe(false);
    // A stringly-typed toggle is not a toggle.
    expect(viewDivergence(state, { filters: { overdue: "false" } }).dirty).toBe(false);
    // A group_by nobody offers falls back to "status".
    expect(viewDivergence(state, { group_by: "phase-of-the-moon" }).dirty).toBe(false);
  });

  it("does not care about the ORDER of a set", () => {
    const saved = toConfig(
      { ...EMPTY_FILTERS, tags: ["bug", "ops"] },
      "status",
      { subGroupBy: "assignee", collapsedLanes: ["a", "b"], showEmptyLanes: false },
      ["status", "due_at"],
    );
    expect(
      viewDivergence(
        live({
          filters: { ...EMPTY_FILTERS, tags: ["ops", "bug"] },
          lanes: {
            subGroupBy: "assignee",
            collapsedLanes: ["b", "a"],
            showEmptyLanes: false,
          },
          shownFields: ["due_at", "status"],
        }),
        saved,
      ),
    ).toEqual({ dirty: false, changed: [] });
  });

  it("treats an assignee's case as noise, because the server does", () => {
    const saved = toConfig({ ...EMPTY_FILTERS, assignee: "Priya@x.io" }, "status");
    expect(
      viewDivergence(
        live({ filters: { ...EMPTY_FILTERS, assignee: "priya@x.io" } }),
        saved,
      ).dirty,
    ).toBe(false);
  });

  it("notices every column being hidden, which is not the default", () => {
    const saved = toConfig(EMPTY_FILTERS, "status", NO_LANES, DEFAULT_SHOWN);
    expect(viewDivergence(live({ shownFields: [] }), saved)).toEqual({
      dirty: true,
      changed: ["fields"],
    });
    // …and the reverse: a view that stored "nothing shown" is dirty the moment
    // a column comes back.
    expect(
      viewDivergence(live({ shownFields: ["status"] }), { shown_fields: [] }).dirty,
    ).toBe(true);
  });

  it("says nothing at all when nothing changed", () => {
    expect(describeDivergence([])).toBe("");
    expect(describeDivergence(["filters"])).toBe("filters");
  });
});

describe("the grouping default is named once", () => {
  it("is what an empty config resolves to", () => {
    // Two places defaulted this as a bare string and had to agree. If they
    // ever stop agreeing, a board re-groups itself on the second visit.
    expect(fromConfig(null).groupBy).toBe(DEFAULT_GROUP_BY);
    expect(fromConfig({}).groupBy).toBe(DEFAULT_GROUP_BY);
    expect(GROUP_OPTIONS).toContain(DEFAULT_GROUP_BY);
  });
});

describe("the watching filter (WS-27bk §9.12.2)", () => {
  it("is off in EMPTY_FILTERS, so nothing is hidden by default", () => {
    expect(EMPTY_FILTERS.watching).toBe(false);
    expect(toQuery(EMPTY_FILTERS).watching).toBeUndefined();
  });

  it("travels as a string on the query and a BOOLEAN in a view", () => {
    // The two shapes differ on purpose. A query string carries only text, and
    // a config is JSON that keeps a boolean a boolean — `fromConfig` refuses a
    // string where a toggle belongs, so a view built from query shape would
    // come back with its toggles silently cleared.
    const on = { ...EMPTY_FILTERS, watching: true };
    expect(toQuery(on).watching).toBe("true");
    expect(toConfig(on, "status").filters).toMatchObject({ watching: true });
  });

  it("survives a saved view round trip", () => {
    const on = { ...EMPTY_FILTERS, watching: true };
    const back = fromConfig(toConfig(on, "status"));
    expect(back.filters.watching).toBe(true);
  });

  it("reads a hand-edited string as OFF rather than as on", () => {
    // `"false"` is truthy. A config that stored the query shape by mistake
    // must not silently switch the filter on for everyone who opens the view.
    expect(fromConfig({ filters: { watching: "true" } }).filters.watching).toBe(false);
    expect(fromConfig({ filters: { watching: "false" } }).filters.watching).toBe(false);
  });

  it("counts as filtering, so the Clear affordance appears", () => {
    expect(isFiltered(EMPTY_FILTERS)).toBe(false);
    expect(isFiltered({ ...EMPTY_FILTERS, watching: true })).toBe(true);
  });

  it("stores nothing when off, so an untouched view stays byte-identical", () => {
    const config = toConfig(EMPTY_FILTERS, "status");
    expect(config.filters).toEqual({});
  });

  it("composes with the other filters rather than replacing them", () => {
    // The whole argument for a filter over a fourth lens.
    const query = toQuery({
      ...EMPTY_FILTERS,
      watching: true,
      overdue: true,
      statusCategory: "in_progress",
    });
    expect(query).toMatchObject({
      watching: "true",
      overdue: "true",
      status_category: "in_progress",
    });
  });
});
