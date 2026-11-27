/**
 * STU-LY-2b fence: column hide keeps the first column, ignores a key that is not a column, and never changes its input.
 *
 * Mutation 1: let `visibleFields` hide the first column. "never hides the first column" turns red.
 * Mutation 2: let `toggleHidden` accept a key that is not a column. "ignores a key that is not a column" turns red.
 * Mutation 3: let `hideNode` change its input. "never changes its input" turns red.
 */
import { describe, expect, it } from "vitest";

import { MAX_HIDDEN, hideNode, hideableKeys, parseHidden, showAll, toggleHidden, visibleFields } from "./columnModel";
import { decodePageState, encodePageState, EMPTY_PAGE_STATE } from "./pageState";

const fields = [{ key: "name" }, { key: "status" }, { key: "when" }, { key: "owner" }];

describe("parseHidden", () => {
  it("reads valid keys once each, and drops junk", () => {
    expect(parseHidden("status,when,status")).toEqual(["status", "when"]);
    expect(parseHidden("Status,1x,,a-b,when")).toEqual(["when"]);
    expect(parseHidden("")).toEqual([]);
    expect(parseHidden(null)).toEqual([]);
    expect(parseHidden(undefined)).toEqual([]);
  });
  it("stops at the cap", () => {
    const many = Array.from({ length: 30 }, (_, i) => `k${i}`).join(",");
    expect(parseHidden(many)).toHaveLength(MAX_HIDDEN);
  });
});

describe("visibleFields", () => {
  it("never hides the first column", () => {
    expect(visibleFields(fields, ["name", "status"]).map((f) => f.key)).toEqual(["name", "when", "owner"]);
  });
  it("keeps the order, and shows all columns for a key that is not one", () => {
    expect(visibleFields(fields, ["ghost"]).map((f) => f.key)).toEqual(["name", "status", "when", "owner"]);
  });
  it("keeps at least one column", () => {
    expect(visibleFields(fields, ["status", "when", "owner"]).map((f) => f.key)).toEqual(["name"]);
    expect(visibleFields([], ["a"])).toEqual([]);
  });
});

describe("toggleHidden", () => {
  it("hides a column and shows it again", () => {
    const a = toggleHidden(EMPTY_PAGE_STATE, "when", fields);
    expect(a.hide).toBe("when");
    const b = toggleHidden(a, "owner", fields);
    expect(b.hide).toBe("when,owner");
    const c = toggleHidden(b, "when", fields);
    expect(c.hide).toBe("owner");
    expect(toggleHidden(c, "owner", fields).hide).toBeUndefined();
  });
  it("ignores the first column and a key that is not a column", () => {
    expect(toggleHidden(EMPTY_PAGE_STATE, "name", fields)).toBe(EMPTY_PAGE_STATE);
    expect(toggleHidden(EMPTY_PAGE_STATE, "ghost", fields)).toBe(EMPTY_PAGE_STATE);
  });
  it("lists the keys that may hide, and shows all again", () => {
    expect(hideableKeys(fields)).toEqual(["status", "when", "owner"]);
    expect(showAll({ ...EMPTY_PAGE_STATE, hide: "when" }).hide).toBeUndefined();
  });
});

describe("hideNode", () => {
  const node = Object.freeze({ type: "template", props: Object.freeze({ name: "dataGrid", data: Object.freeze({ title: "T", fields: Object.freeze([...fields]) }) }) });
  it("leaves the hidden columns out of the fields and never changes its input", () => {
    const out = hideNode(node, ["when", "name"]);
    expect((out.props.data.fields as { key: string }[]).map((f) => f.key)).toEqual(["name", "status", "owner"]);
    expect(node.props.data.fields).toHaveLength(4);
    expect(out.props.data.title).toBe("T");
  });
  it("returns the node itself when nothing hides or the node has no field list", () => {
    expect(hideNode(node, [])).toBe(node);
    expect(hideNode(node, ["ghost"])).toBe(node);
    const none = { props: { data: { title: "x" } } };
    expect(hideNode(none, ["a"])).toBe(none);
  });
});

describe("the hide key in the URL", () => {
  it("round-trips, and drops a value with a bad key", () => {
    const q = encodePageState({ ...EMPTY_PAGE_STATE, hide: "status,when" });
    expect(q).toContain("hide=status%2Cwhen");
    expect(decodePageState(q).hide).toBe("status,when");
    expect(decodePageState("hide=Bad!").hide).toBeUndefined();
    expect(decodePageState("hide=a,B,c").hide).toBe("a,c");
  });
});
