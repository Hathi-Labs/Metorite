/**
 * STU-AF4 fence: the list bar model. Chips follow the field type and the hot rule, the door input is
 * valid for the grammar of `studio_query.py`, and every choice survives a reload through the URL.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { decodePageState, encodePageState, type PageState } from "./pageState";
import {
  CHIP_TYPES, MAX_CONDITIONS, SORT_TYPES, activeCount, barOf, barTarget, chipFor, chipsOf, clearedBar, layoutsFor,
  parseRange, rangeValue, searchKeyOf, sortOptions, toListArgs, withFilter, withGroup, withLayout, withQ, withSort,
  type BarField,
} from "./filterBarModel";
import { mapView } from "./viewBlocks";
import type { ViewDef } from "./manifest";
import type { ObjectRow } from "./realDoor";

const F = (key: string, type: string, extra: Partial<BarField> = {}): BarField => ({ key, type, hot: true, ...extra });
const FIELDS: BarField[] = [
  F("title", "text"),
  F("status", "select", { options: ["open", "done"] }),
  F("urgent", "checkbox"),
  F("cost", "number"),
  F("price", "money"),
  F("due", "date"),
  F("tags", "select", { many: true, options: ["a"] }),
  F("notes", "longText"),
  F("owner", "person"),
  F("score", "rating"),
  F("cold", "text", { hot: false }),
];
const EMPTY: PageState = { selected: null, filters: {}, tab: null };
const bar = (s: Partial<PageState>) => barOf({ ...EMPTY, ...s });

describe("chips by field type", () => {
  it("gives a chip to a hot field of a type the door filters", () => {
    expect(chipFor(F("status", "select", { options: ["a"] }))?.kind).toBe("select");
    expect(chipFor(F("urgent", "checkbox"))?.kind).toBe("checkbox");
    expect(chipFor(F("cost", "number"))?.kind).toBe("number");
    expect(chipFor(F("due", "date"))?.kind).toBe("date");
    expect(chipFor(F("when", "datetime"))?.kind).toBe("date");
    expect(chipFor(F("mail", "email"))?.kind).toBe("text");
  });

  it("gives no chip to a type the door cannot filter, to a many field, to a cold field, to money", () => {
    for (const t of ["longText", "person", "rating", "relation", "money", "duration", "file"]) {
      expect(chipFor(F("x", t))).toBeNull();
    }
    expect(chipFor(F("tags", "select", { many: true, options: ["a"] }))).toBeNull();
    expect(chipFor(F("cold", "text", { hot: false }))).toBeNull();
    expect(chipFor(F("x", "select", { options: [] }))).toBeNull();
    expect(chipFor(F("x", "text", { lines: "parent" }))).toBeNull();
  });

  it("keeps the search field out of the chips", () => {
    expect(searchKeyOf(FIELDS)).toBe("title");
    expect(chipsOf(FIELDS).map((c) => c.key)).toEqual(["status", "urgent", "cost", "due"]);
    expect(searchKeyOf([F("cold", "text", { hot: false })])).toBeNull();
  });

  it("offers a sort for hot sortable fields only", () => {
    expect(sortOptions(FIELDS).map((s) => s.key)).toEqual(["title", "status", "cost", "price", "due"]);
  });

  it("matches the closed tables of studio_query.py", () => {
    const py = readFileSync(resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_query.py"), "utf-8");
    const ops = /OPS_BY_TYPE[^{]*\{([\s\S]*?)\n\}/.exec(py)?.[1] ?? "";
    const types = [...ops.matchAll(/^\s+"(\w+)":/gm)].map((m) => m[1]);
    for (const t of Object.keys(CHIP_TYPES)) expect(types).toContain(t);
    for (const t of SORT_TYPES) expect(types).toContain(t);
    expect(Number(/MAX_FILTERS = (\d+)/.exec(py)?.[1])).toBe(MAX_CONDITIONS);
    // the types with a chip in the door: every type here has ops, and the python sort set drops the same three.
    expect(py).toContain('SORT_TYPES: frozenset[str] = frozenset(OPS_BY_TYPE) - {"checkbox", "relation", "person"}');
    expect(SORT_TYPES.has("checkbox")).toBe(false);
  });
});

describe("toListArgs", () => {
  it("builds the door filter from the search and each chip", () => {
    const b = bar({
      q: "  oil ",
      filters: { status: "open", urgent: "no", cost: rangeValue("5", "9.5"), due: rangeValue("2026-01-01", "") },
      sort: "-due",
    });
    expect(toListArgs(FIELDS, b)).toEqual({
      filter: [
        { field: "title", op: "contains", value: "oil" },
        { field: "status", op: "eq", value: "open" },
        { field: "urgent", op: "eq", value: false },
        { field: "cost", op: "gte", value: 5 },
        { field: "cost", op: "lte", value: 9.5 },
        { field: "due", op: "gte", value: "2026-01-01" },
      ],
      sort: { field: "due", dir: "desc" },
    });
    expect(toListArgs(FIELDS, bar({}))).toEqual({});
  });

  it("drops a value that no chip can hold, so a hostile URL gives a valid query", () => {
    const b = bar({
      filters: { status: "gone", cold: "x", owner: "a@b.c", cost: "abc..1e9", due: "2026-13..soon", urgent: "maybe", __proto__: "x" },
      sort: "-urgent",
    });
    expect(toListArgs(FIELDS, b)).toEqual({});
    expect(toListArgs(FIELDS, bar({ sort: "cold" }))).toEqual({});
  });

  it("holds at most eight conditions", () => {
    const many = Array.from({ length: 9 }, (_, i) => F(`n${i}`, "number"));
    const filters = Object.fromEntries(many.map((f) => [f.key, rangeValue("1", "2")]));
    expect(toListArgs(many, bar({ filters })).filter).toHaveLength(MAX_CONDITIONS);
  });

  it("counts the fields that filter", () => {
    expect(activeCount(FIELDS, bar({ q: "x", filters: { status: "open", cost: rangeValue("1", "2") } }))).toBe(3);
  });
});

describe("the URL", () => {
  it("keeps a filter, a sort, a layout, a group and the search across a reload", () => {
    let s: PageState = { ...EMPTY, page: "visits" };
    s = withQ(s, " oil ");
    s = withFilter(s, "status", "open");
    s = withFilter(s, "due", rangeValue("2026-01-01", "2026-02-01"));
    s = withSort(s, "-due");
    s = withLayout(s, "board");
    s = withGroup(s, "status");
    const url = encodePageState(s);
    const back = decodePageState(url);
    expect(back).toMatchObject({ page: "visits", q: "oil", sort: "-due", layout: "board", group: "status" });
    expect(back.filters).toEqual({ status: "open", due: "2026-01-01..2026-02-01" });
    expect(encodePageState(back)).toBe(url);
    expect(toListArgs(FIELDS, barOf(back))).toEqual(toListArgs(FIELDS, barOf(s)));
  });

  it("clears one choice and keeps the others", () => {
    let s: PageState = withLayout(withSort(withFilter(withQ(EMPTY, "a"), "status", "open"), "title"), "board");
    s = withFilter(s, "status", "");
    expect(s.filters).toEqual({});
    s = clearedBar(s);
    expect(s.q).toBeUndefined();
    expect(s.sort).toBe("title");
    expect(s.layout).toBe("board");
    expect(withSort(s, null).sort).toBeUndefined();
  });

  it("refuses a bad sort, layout and group in a link", () => {
    const s = decodePageState("sort=-A%20b&layout=gallery&group=Bad-Name&q=%00");
    expect(s.sort).toBeUndefined();
    expect(s.layout).toBeUndefined();
    expect(s.group).toBeUndefined();
    expect(s.q).toBeUndefined();
  });
});

describe("ranges, layouts and the target", () => {
  it("reads and writes a range", () => {
    expect(rangeValue("", "")).toBe("");
    expect(parseRange(rangeValue("1", ""))).toEqual({ from: "1", to: "" });
    expect(parseRange(rangeValue("", "9"))).toEqual({ from: "", to: "9" });
    expect(parseRange(undefined)).toEqual({ from: "", to: "" });
  });

  it("offers the board only when a select field can make lanes", () => {
    expect(layoutsFor([])).toEqual(["table"]);
    expect(layoutsFor(["status"])).toEqual(["table", "board"]);
  });

  it("picks the first table or board that reads an object", () => {
    const views = [{ id: "a", block: "chart" }, { id: "b", block: "table" }, { id: "c", block: "board" }];
    expect(barTarget([{ view: "a" }, { view: "b" }, { view: "c" }], views, () => "visit")?.view.id).toBe("b");
    expect(barTarget([{ view: "a" }], views, () => "visit")).toBeNull();
    expect(barTarget([{ view: "b" }], views, () => null)).toBeNull();
  });
});

describe("the lens of mapView", () => {
  const object = {
    name: "visit", label: "Visit", rules: [],
    fields: [
      { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: true },
      { key: "status", type: "select", label: "Status", required: false, many: false, options: ["open", "done"], to: null, hot: true },
      { key: "stage", type: "select", label: "Stage", required: false, many: false, options: ["a", "b"], to: null, hot: true },
    ],
  } as unknown as ObjectRow;
  const actions = [{ id: "list_visits", uses: "object:visit.list" }];
  const table: ViewDef = { id: "t", block: "table", object: "visit" };
  const sourceOf = (m: ReturnType<typeof mapView>) =>
    (m as { node: { props: { source?: { args: Record<string, unknown> }; data?: { groupBy?: string } } } }).node.props;

  it("puts the filter and the sort in the list read, and only for the named view", () => {
    const args = { filter: [{ field: "title", op: "contains", value: "x" }], sort: { field: "title", dir: "asc" as const } };
    const m = mapView(table, { actions, objects: [object], owner: true, lens: { view: "t", args } });
    expect(sourceOf(m).source?.args).toEqual({ limit: 100, ...args });
    const other = mapView(table, { actions, objects: [object], owner: true, lens: { view: "other", args } });
    expect(sourceOf(other).source?.args).toEqual({ limit: 100 });
  });

  it("draws the same rows as a board, with the lanes that the group names", () => {
    const m = mapView(table, { actions, objects: [object], owner: true, lens: { view: "t", layout: "board", group: "stage" } });
    expect(JSON.stringify(m)).toContain('"groupBy":"stage"');
    const first = mapView(table, { actions, objects: [object], owner: true, lens: { view: "t", layout: "board" } });
    expect(JSON.stringify(first)).toContain('"groupBy":"status"');
    const asTable = mapView({ ...table, block: "board" }, { actions, objects: [object], owner: true, lens: { view: "t", layout: "table" } });
    expect(JSON.stringify(asTable)).not.toContain("groupBy");
  });
});
