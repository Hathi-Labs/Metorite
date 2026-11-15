/**
 * STU-LY-7b fence: the period of a dashboard adds one date filter, and a tile links to the page that lists its object.
 *
 * Mutation 1: let `periodFilter` return a filter for `all`. "all time adds no filter" turns red.
 * Mutation 2: let `periodNode` change its input. "never changes its input" turns red.
 * Mutation 3: let `tileTarget` return the page that it starts from. "never returns the page it starts from" turns red.
 */
import { describe, expect, it } from "vitest";

import { PERIODS, isPeriod, periodFieldOf, periodFilter, periodNode, tileTarget, withPeriod } from "./periodModel";
import { encodePageState, decodePageState, EMPTY_PAGE_STATE } from "./pageState";

const NOW = new Date("2026-11-15T10:00:00Z");

describe("periods", () => {
  it("names all time, 7, 30 and 90 days", () => {
    expect(PERIODS.map((p) => p.id)).toEqual(["all", "7d", "30d", "90d"]);
    expect(isPeriod("30d")).toBe(true);
    expect(isPeriod("1y")).toBe(false);
  });
});

describe("periodFieldOf", () => {
  it("takes the first hot date or datetime field", () => {
    expect(periodFieldOf([{ key: "n", type: "text", hot: true }, { key: "d", type: "date", hot: true }, { key: "e", type: "datetime", hot: true }])).toBe("d");
  });
  it("skips a field that is not hot, a field of many values and a lines relation", () => {
    expect(periodFieldOf([{ key: "d", type: "date" }, { key: "m", type: "date", hot: true, many: true }, { key: "l", type: "date", hot: true, lines: "x" }])).toBeNull();
    expect(periodFieldOf(null)).toBeNull();
    expect(periodFieldOf([])).toBeNull();
  });
});

describe("periodFilter", () => {
  it("gives one gte filter on the UTC day", () => {
    expect(periodFilter("d", "7d", NOW)).toEqual({ field: "d", op: "gte", value: "2026-11-08" });
    expect(periodFilter("d", "30d", NOW)).toEqual({ field: "d", op: "gte", value: "2026-10-16" });
    expect(periodFilter("d", "90d", NOW)).toEqual({ field: "d", op: "gte", value: "2026-08-17" });
  });
  it("all time adds no filter, and neither do an unknown period, no field or a bad date", () => {
    expect(periodFilter("d", "all", NOW)).toBeNull();
    expect(periodFilter("d", undefined, NOW)).toBeNull();
    expect(periodFilter("d", "1y", NOW)).toBeNull();
    expect(periodFilter(null, "7d", NOW)).toBeNull();
    expect(periodFilter("d", "7d", new Date("nope"))).toBeNull();
  });
});

describe("periodNode", () => {
  const node = Object.freeze({ type: "stat", props: Object.freeze({ title: "T", source: Object.freeze({ action: "object:v.aggregate", args: Object.freeze({ filter: Object.freeze([{ field: "s", op: "eq", value: "open" }]) }) }) }) });
  const f = { field: "d", op: "gte" as const, value: "2026-11-08" };
  it("appends the filter to the existing filters and never changes its input", () => {
    const out = periodNode(node, f);
    expect(out.props.source.args.filter).toEqual([{ field: "s", op: "eq", value: "open" }, f]);
    expect(node.props.source.args.filter).toHaveLength(1);
    expect(out).not.toBe(node);
  });
  it("adds the first filter to a source with no args", () => {
    const bare = { props: { source: { action: "object:v.list" } } };
    expect(periodNode(bare, f).props.source.args).toEqual({ filter: [f] });
  });
  it("returns the node itself for no filter or no source", () => {
    expect(periodNode(node, null)).toBe(node);
    const none = { props: { title: "x" } };
    expect(periodNode(none, f)).toBe(none);
  });
});

describe("withPeriod and the URL", () => {
  it("sets a period, and takes the key away for all time or an unknown one", () => {
    expect(withPeriod(EMPTY_PAGE_STATE, "30d").period).toBe("30d");
    expect(withPeriod({ ...EMPTY_PAGE_STATE, period: "30d" }, "all").period).toBeUndefined();
    expect(withPeriod(EMPTY_PAGE_STATE, "1y").period).toBeUndefined();
  });
  it("keeps the period through the URL, and drops a value that is not a period", () => {
    const q = encodePageState(withPeriod(EMPTY_PAGE_STATE, "7d"));
    expect(q).toContain("period=7d");
    expect(decodePageState(q).period).toBe("7d");
    expect(decodePageState("period=1y").period).toBeUndefined();
  });
});

describe("tileTarget", () => {
  const views = [{ id: "open", block: "stat" }, { id: "all", block: "table" }, { id: "pipe", block: "board" }, { id: "rep", block: "chart" }];
  const objectOf = (id: string) => (id === "pipe" || id === "all" || id === "open" || id === "rep" ? "visit" : null);
  const pages = [{ id: "home", viewIds: ["open", "rep"] }, { id: "list", viewIds: ["all"] }, { id: "board", viewIds: ["pipe"] }];
  it("gives the first other page that holds a table or a board of the object", () => {
    expect(tileTarget(pages, views, objectOf, "home", "visit")).toBe("list");
  });
  it("never returns the page it starts from, and gives null for no list or no object", () => {
    expect(tileTarget(pages, views, objectOf, "list", "visit")).toBe("board");
    expect(tileTarget([{ id: "home", viewIds: ["open"] }], views, objectOf, "home", "visit")).toBeNull();
    expect(tileTarget(pages, views, objectOf, "home", null)).toBeNull();
    expect(tileTarget(pages, views, objectOf, "home", "other")).toBeNull();
  });
});
