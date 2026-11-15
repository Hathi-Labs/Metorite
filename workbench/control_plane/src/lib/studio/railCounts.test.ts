/** STU-AF3b fence: the source of a count, and the label that the rail shows. */
import { describe, expect, it } from "vitest";

import { rowsMeta, withMeta } from "./dataDoor";
import type { ViewDef } from "./manifest";
import type { PageModel } from "./pageModel";
import { COUNT_CAP, countLabel, countSourceFor, pageObject, pageViewIds, sourceWithMe } from "./railCounts";

const OBJECTS = [{ name: "task", fields: [{ key: "owner", type: "person" }, { key: "status", type: "select" }] }];
const ACTIONS = [{ id: "list_tasks", uses: "object:task.list" }];
const VIEWS: ViewDef[] = [
  { id: "chart", block: "chart", source: { action: "list_tasks", args: { kind: "bar" } } },
  { id: "mine", block: "table", object: "task",
    source: { action: "list_tasks", args: { limit: 50, kind: "x",
      filter: [{ field: "owner", op: "eq", value: "me" }], sort: { field: "status", dir: "asc" } } } },
  { id: "all", block: "board", object: "task" },
  { id: "ghost", block: "table", object: "nothing" },
];
const page = (over: Partial<PageModel>): PageModel => ({
  id: "p", title: "P", cols: 1, tabs: [], cells: [], side: null, ...over,
});

describe("countSourceFor", () => {
  it("takes the first table or board of the page, keeps its filter, drops the sort and sets limit 1", () => {
    const src = countSourceFor(page({ cells: [{ view: "chart", span: 1 }, { view: "mine", span: 1 }, { view: "all", span: 1 }] }),
      VIEWS, OBJECTS, ACTIONS);
    expect(src).toEqual({ action: "object:task.list", args: { limit: 1, filter: [{ field: "owner", op: "eq", value: "me" }] } });
  });
  it("looks in the tabs, then the side", () => {
    const tabbed = page({ tabs: [{ id: "t", title: "T", cells: [{ view: "all", span: 1 }] }] });
    expect(countSourceFor(tabbed, VIEWS, OBJECTS)?.args).toEqual({ limit: 1 });
    expect(pageViewIds(page({ cells: [{ view: "a", span: 1 }], side: "s" }))).toEqual(["a", "s"]);
    expect(countSourceFor(page({ side: "all" }), VIEWS, OBJECTS)?.action).toBe("object:task.list");
  });
  it("gives null with no table or board, or an unknown object", () => {
    expect(countSourceFor(page({ cells: [{ view: "chart", span: 1 }] }), VIEWS, OBJECTS, ACTIONS)).toBeNull();
    expect(countSourceFor(page({ cells: [{ view: "ghost", span: 1 }] }), VIEWS, OBJECTS)).toBeNull();
    expect(countSourceFor(page({}), VIEWS, OBJECTS)).toBeNull();
  });
  it("names the object of the page", () => {
    expect(pageObject(page({ cells: [{ view: "ghost", span: 1 }] }), VIEWS)).toBe("nothing");
    expect(pageObject(page({ cells: [{ view: "chart", span: 1 }] }), VIEWS)).toBeNull();
  });
});

describe("sourceWithMe", () => {
  const src = { action: "object:task.list", args: { limit: 1, filter: [{ field: "owner", op: "eq", value: "me" }] } };
  it("puts the email in a person filter and waits for the email", () => {
    expect(sourceWithMe(src, "Ann@x.io", OBJECTS[0])?.args?.filter).toEqual([{ field: "owner", op: "eq", value: "ann@x.io" }]);
    expect(sourceWithMe(src, null, OBJECTS[0])).toBeNull();
  });
});

describe("countLabel", () => {
  it("shows an exact total, grouped", () => {
    expect(countLabel({ total: 7 })).toBe("7");
    expect(countLabel({ total: 1234567 }, "indian")).toBe("12,34,567");
    expect(countLabel({ total: 1234567 }, "international")).toBe("1,234,567");
    expect(countLabel({ total: 0 })).toBe("0");
  });
  it("shows 10,000 exact, and 10,000+ when the read says at least", () => {
    expect(countLabel({ total: COUNT_CAP }, "international")).toBe("10,000");
    expect(countLabel({ atLeast: 10001 }, "international")).toBe("10,000+");
    expect(countLabel({ atLeast: 10001, total: 10001 }, "international")).toBe("10,000+");
  });
  it("shows nothing for loading, an error, a refusal or a missing total", () => {
    expect(countLabel(null)).toBeNull();
    expect(countLabel(undefined)).toBeNull();
    expect(countLabel({})).toBeNull();
    expect(countLabel({ total: Number.NaN })).toBeNull();
    expect(countLabel({ total: -1 })).toBeNull();
    // Rows with no paging facts, as an answer with no total gives.
    expect(countLabel(rowsMeta(withMeta([], {})))).toBeNull();
  });
  it("reads the meta that the door attaches to its rows", () => {
    expect(countLabel(rowsMeta(withMeta([], { total: 42 })), "international")).toBe("42");
    expect(countLabel(rowsMeta(withMeta([], { atLeast: 10001 })), "international")).toBe("10,000+");
  });
});
