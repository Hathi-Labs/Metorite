/**
 * STU-AF-10b-1 fence: the saved rows model. A state goes to a spec and back with no change for each
 * chip kind. A hostile spec gives a valid empty state. The specs of the fixture pass the server check
 * (`tests/unit/test_studio_saved_view_rows_parity.py`). This test compares the fixture and never writes it.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { chipList, orderViews } from "@/lib/savedViews";

import type { BarField } from "./filterBarModel";
import type { PageState } from "./pageState";
import {
  activeViewId, droppedNote, specToState, stateToSpec, toSeamView, viewsForObject, type WireView,
} from "./savedViewRows";

const F = (key: string, type: string, extra: Partial<BarField> = {}): BarField => ({ key, type, hot: true, ...extra });
const FIELDS: BarField[] = [
  F("title", "text"),
  F("note", "text"),
  F("status", "select", { options: ["open", "done"] }),
  F("urgent", "checkbox"),
  F("cost", "number"),
  F("due", "date"),
  F("seen", "date"),
  F("tags", "select", { many: true, options: ["a"] }),
  F("owner", "person"),
  F("price", "money"),
  F("cold", "text", { hot: false }),
];
const S = (s: Partial<PageState>): PageState => ({ selected: null, filters: {}, tab: null, ...s });

const CASES: Record<string, PageState> = {
  empty: S({}),
  select: S({ filters: { status: "open" } }),
  checkbox_yes: S({ filters: { urgent: "yes" } }),
  checkbox_no: S({ filters: { urgent: "no" } }),
  number_range: S({ filters: { cost: "2..9.5" } }),
  number_from: S({ filters: { cost: "2.." } }),
  number_to: S({ filters: { cost: "..9" } }),
  date_range: S({ filters: { due: "2026-01-01..2026-02-01" } }),
  text_chip: S({ filters: { note: "late" } }),
  search: S({ q: "bolt" }),
  sort_asc: S({ sort: "cost" }),
  sort_desc: S({ sort: "-cost" }),
  layout_board: S({ layout: "board" }),
  layout_table: S({ layout: "table" }),
  mixed_eight: S({
    q: "bolt",
    filters: { note: "late", status: "done", urgent: "yes", cost: "1..5", due: "2026-01-01..2026-02-01" },
    sort: "-due",
    layout: "board",
  }),
};

const freeze = <T,>(v: T): T => {
  if (v && typeof v === "object") {
    Object.values(v as object).forEach(freeze);
    Object.freeze(v);
  }
  return v;
};

describe("the round trip", () => {
  for (const [name, state] of Object.entries(CASES)) {
    it(`holds for ${name}`, () => {
      const spec = stateToSpec(state, FIELDS);
      const back = specToState(spec, FIELDS);
      expect(back.dropped).toBe(0);
      expect(back.state).toEqual(state);
    });
  }

  it("makes eight conditions from the mixed state", () => {
    expect(stateToSpec(CASES.mixed_eight, FIELDS).filter).toHaveLength(8);
  });

  it("makes one range chip of a gte and a lte, in any order", () => {
    const spec = { filter: [{ field: "cost", op: "lte", value: 9 }, { field: "cost", op: "gte", value: 2 }] };
    expect(specToState(spec, FIELDS).state.filters).toEqual({ cost: "2..9" });
  });

  it("makes a lone gte or lte a half range", () => {
    expect(specToState({ filter: [{ field: "due", op: "gte", value: "2026-01-01" }] }, FIELDS).state.filters).toEqual({ due: "2026-01-01.." });
    expect(specToState({ filter: [{ field: "due", op: "lte", value: "2026-01-01" }] }, FIELDS).state.filters).toEqual({ due: "..2026-01-01" });
  });

  it("reads an in with one value as the select chip", () => {
    const r = specToState({ filter: [{ field: "status", op: "in", value: ["done"] }] }, FIELDS);
    expect(r.state.filters).toEqual({ status: "done" });
    expect(r.dropped).toBe(0);
  });

  it("reads a contains on the search key as q", () => {
    expect(specToState({ filter: [{ field: "title", op: "contains", value: "x" }] }, FIELDS).state.q).toBe("x");
  });

  it("does not store the group, a selection or a tab", () => {
    const spec = stateToSpec(S({ group: "status", selected: "r1", tab: "t", filters: { status: "open" } }), FIELDS);
    expect(spec).toEqual({ filter: [{ field: "status", op: "eq", value: "open" }] });
  });
});

describe("a condition with no chip", () => {
  it("drops an unknown field and counts it", () => {
    const r = specToState({ filter: [{ field: "ghost", op: "eq", value: 1 }, { field: "status", op: "eq", value: "open" }] }, FIELDS);
    expect(r.dropped).toBe(1);
    expect(r.state.filters).toEqual({ status: "open" });
  });

  it("drops a relation, a many field, a person, a money field and a cold field", () => {
    const fields = [...FIELDS, F("rel", "relation")];
    for (const field of ["rel", "tags", "owner", "price", "cold"]) {
      const r = specToState({ filter: [{ field, op: "eq", value: "a" }] }, fields);
      expect(r.dropped).toBe(1);
      expect(r.state.filters).toEqual({});
    }
  });

  it("drops an op that the chip cannot show, and a value it cannot hold", () => {
    const bad = [
      { field: "status", op: "ne", value: "open" },
      { field: "status", op: "eq", value: "gone" },
      { field: "status", op: "in", value: ["open", "done"] },
      { field: "cost", op: "gt", value: 1 },
      { field: "cost", op: "gte", value: "1" },
      { field: "due", op: "gte", value: "tomorrow" },
      { field: "urgent", op: "eq", value: "yes" },
      { field: "note", op: "eq", value: "x" },
      { field: "title", op: "eq", value: "x" },
    ];
    const r = specToState({ filter: bad }, FIELDS);
    expect(r.dropped).toBe(bad.length);
    expect(r.state).toEqual(S({}));
  });

  it("drops a second condition on one slot", () => {
    const r = specToState({ filter: [{ field: "status", op: "eq", value: "open" }, { field: "status", op: "eq", value: "done" }] }, FIELDS);
    expect(r.dropped).toBe(1);
    expect(r.state.filters).toEqual({ status: "open" });
  });

  it("drops a sort on a field with no sort, and a bad layout", () => {
    const r = specToState({ sort: { field: "urgent", dir: "asc" }, layout: "gallery" }, FIELDS);
    expect(r.dropped).toBe(2);
    expect(r.state).toEqual(S({}));
  });

  it("keeps the first eight of nine conditions", () => {
    const nine = [
      { field: "title", op: "contains", value: "a" },
      { field: "note", op: "contains", value: "b" },
      { field: "status", op: "eq", value: "open" },
      { field: "urgent", op: "eq", value: true },
      { field: "cost", op: "gte", value: 1 },
      { field: "cost", op: "lte", value: 2 },
      { field: "due", op: "gte", value: "2026-01-01" },
      { field: "due", op: "lte", value: "2026-02-01" },
      { field: "seen", op: "gte", value: "2026-03-01" },
    ];
    const r = specToState({ filter: nine }, FIELDS);
    expect(r.dropped).toBe(1);
    expect(r.state.filters.seen).toBeUndefined();
    expect(Object.keys(r.state.filters)).toHaveLength(5);
  });

  it("does not count columns, a stored part that the bar does not show", () => {
    expect(specToState({ columns: ["a"] }, FIELDS).dropped).toBe(0);
  });
});

describe("a hostile spec", () => {
  const huge = "x".repeat(10_000);
  const hostile: [string, unknown][] = [
    ["null", null],
    ["a string", "spec"],
    ["a number", 5],
    ["an array", []],
    ["filter 5", { filter: 5 }],
    ["items null", { filter: [null, undefined, 3, "a", []] }],
    ["field a number", { filter: [{ field: 5, op: "eq", value: 1 }] }],
    ["op a number", { filter: [{ field: "status", op: 5, value: 1 }] }],
    ["a huge value", { filter: [{ field: "title", op: "contains", value: huge }, { field: "note", op: "contains", value: huge }] }],
    ["a huge sort", { sort: { field: huge, dir: "asc" } }],
    ["sort a string", { sort: "cost" }],
    ["a getter that throws", Object.defineProperty({}, "filter", { enumerable: true, get() { throw new Error("no"); } })],
  ];
  for (const [name, spec] of hostile) {
    it(`gives a valid empty state for ${name}`, () => {
      const r = specToState(spec, FIELDS);
      expect(r.state).toEqual(S({}));
      expect(Object.keys(r.state.filters)).toHaveLength(0);
    });
  }

  it("counts the part that it drops", () => {
    expect(specToState("spec", FIELDS).dropped).toBe(1);
    expect(specToState({ filter: 5 }, FIELDS).dropped).toBe(1);
    expect(specToState({ filter: [{ field: "title", op: "contains", value: "x".repeat(10_000) }] }, FIELDS).dropped).toBe(1);
  });

  it("ignores a __proto__ key and counts it", () => {
    const spec = JSON.parse('{"__proto__": {"polluted": true}, "filter": [{"field": "status", "op": "eq", "value": "open"}]}');
    const r = specToState(spec, FIELDS);
    expect(r.dropped).toBe(1);
    expect(r.state.filters).toEqual({ status: "open" });
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("makes no spec of a hostile state and does not throw", () => {
    const hostileState = { filters: { status: 5, __proto__x: "a", constructor: "b" }, q: 7, sort: {}, layout: "gallery" } as never;
    expect(() => stateToSpec(hostileState, FIELDS)).not.toThrow();
    expect(stateToSpec(hostileState, FIELDS)).toEqual({ filter: [] });
    expect(stateToSpec(null as never, FIELDS)).toEqual({ filter: [] });
  });
});

describe("the input stays whole", () => {
  it("does not change the state or the spec", () => {
    const state = freeze(structuredClone(CASES.mixed_eight));
    const spec = freeze(structuredClone(stateToSpec(CASES.mixed_eight, FIELDS)));
    const fields = freeze(structuredClone(FIELDS));
    expect(() => stateToSpec(state, fields)).not.toThrow();
    expect(() => specToState(spec, fields)).not.toThrow();
    expect(stateToSpec(state, fields)).toEqual(stateToSpec(CASES.mixed_eight, FIELDS));
    expect(specToState(spec, fields).state).toEqual(CASES.mixed_eight);
  });

  it("is deterministic", () => {
    expect(stateToSpec(CASES.mixed_eight, FIELDS)).toEqual(stateToSpec(CASES.mixed_eight, FIELDS));
  });
});

describe("droppedNote", () => {
  it("says nothing for zero", () => {
    expect(droppedNote(0, true)).toBeNull();
    expect(droppedNote(0, false)).toBeNull();
  });
  it("says one condition in the singular", () => {
    expect(droppedNote(1, true)).toBe("1 condition does not apply to you");
  });
  it("says the count in the plural", () => {
    expect(droppedNote(2, true)).toBe("2 conditions do not apply to you");
  });
  it("shows nothing to an owner", () => {
    expect(droppedNote(1, false)).toBeNull();
    expect(droppedNote(5, false)).toBeNull();
  });
  it("shows nothing for a bad count", () => {
    expect(droppedNote(-1, true)).toBeNull();
    expect(droppedNote(Number.NaN, true)).toBeNull();
  });
});

const wire = (over: Partial<WireView>): WireView => ({
  id: "v1", object: "visit", name: "Open", shared: false, mine: true, spec: {}, position: 0, dropped: 0,
  created_at: "2026-11-14T00:00:00Z", updated_at: null, ...over,
});

describe("toSeamView", () => {
  it("keeps the spec, the object and the dropped count", () => {
    const v = toSeamView(wire({ spec: { filter: [] }, dropped: 2 }));
    expect(v).toMatchObject({ id: "v1", name: "Open", shared: false, mine: true, position: 0, object: "visit", dropped: 2, spec: { filter: [] } });
  });
  it("gives a safe value for a bad part", () => {
    const v = toSeamView({ id: 3, name: null, spec: "x", position: "a", dropped: -4 } as never);
    expect(v).toEqual({ id: "", name: "", shared: false, mine: false, position: 0, object: "", spec: {}, dropped: 0 });
    expect(() => toSeamView(null as never)).not.toThrow();
  });
  it("feeds the seam", () => {
    const views = [
      toSeamView(wire({ id: "a", name: "Shared", shared: true, mine: false, position: 1 })),
      toSeamView(wire({ id: "b", name: "Mine", position: 2 })),
    ];
    expect(orderViews(views).map((v) => v.id)).toEqual(["b", "a"]);
    expect(chipList("All", views, "b").map((c) => [c.id, c.active])).toEqual([[null, false], ["b", true], ["a", false]]);
  });
});

describe("viewsForObject", () => {
  it("keeps the views of one object in order", () => {
    const views = [toSeamView(wire({ id: "a" })), toSeamView(wire({ id: "b", object: "site" })), toSeamView(wire({ id: "c" }))];
    expect(viewsForObject(views, "visit").map((v) => v.id)).toEqual(["a", "c"]);
    expect(viewsForObject(null, "visit")).toEqual([]);
  });
});

describe("activeViewId", () => {
  const views = [
    toSeamView(wire({ id: "open", spec: stateToSpec(CASES.select, FIELDS) })),
    toSeamView(wire({ id: "mixed", spec: stateToSpec(CASES.mixed_eight, FIELDS) })),
  ];
  it("finds the view that the state equals", () => {
    expect(activeViewId(CASES.select, views, FIELDS)).toBe("open");
    expect(activeViewId(CASES.mixed_eight, views, FIELDS)).toBe("mixed");
  });
  it("ignores the key order of the spec and a part that the state does not hold", () => {
    const spec = { layout: "board", filter: [{ value: "open", op: "eq", field: "status" }], columns: ["title"] };
    expect(activeViewId(S({ filters: { status: "open" }, layout: "board", group: "status", selected: "r1" }), [{ id: "k", spec }], FIELDS)).toBe("k");
  });
  it("finds no view for another state", () => {
    expect(activeViewId(S({ filters: { status: "done" } }), views, FIELDS)).toBeNull();
    expect(activeViewId(S({}), views, FIELDS)).toBeNull();
    expect(activeViewId(S({}), null, FIELDS)).toBeNull();
  });
});

describe("the fixture of the server check", () => {
  const FIXTURE = resolve(__dirname, "__fixtures__/savedViewRows_specs.json");
  it("equals the specs of the cases", () => {
    const want = Object.entries(CASES).map(([name, state]) => ({ name, spec: stateToSpec(state, FIELDS) }));
    expect(JSON.parse(readFileSync(FIXTURE, "utf-8"))).toEqual(want);
  });
});
