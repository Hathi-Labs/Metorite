import { describe, expect, it } from "vitest";

import { EMPTY_PAGE_STATE, boundPageState, decodePageState, encodePageState } from "./pageState";

describe("pageState", () => {
  it("round-trips a state", () => {
    const state = { selected: "t-9", filters: { owner: "Asha", status: "open now" }, tab: "board" };
    expect(decodePageState(encodePageState(state))).toEqual(state);
  });

  it("gives one string to one state, whatever the key order", () => {
    const a = encodePageState({ selected: null, filters: { b: "2", a: "1" }, tab: null });
    const b = encodePageState({ selected: null, filters: { a: "1", b: "2" }, tab: null });
    expect(a).toBe(b);
  });

  it("drops empty values and ignores unknown keys", () => {
    expect(encodePageState(EMPTY_PAGE_STATE)).toBe("");
    expect(encodePageState({ selected: "", filters: { a: "" }, tab: "" })).toBe("");
    expect(decodePageState("")).toEqual(EMPTY_PAGE_STATE);
    expect(decodePageState("zzz=1&f.=x&f.a=")).toEqual(EMPTY_PAGE_STATE);
  });
});

describe("boundPageState", () => {
  const page = { tabs: [{ id: "one" }, { id: "two" }] };

  it("keeps a state that is inside the bounds", () => {
    const state = { selected: "t-9", filters: { owner: "Asha" }, tab: "two" };
    expect(boundPageState(state, page)).toEqual(state);
  });

  it("keeps 8 filters of 10,000, in sorted order, and the string stays short", () => {
    const q = new URLSearchParams();
    for (let i = 0; i < 10_000; i++) q.set(`f.k${String(i).padStart(5, "0")}`, "v");
    const s = decodePageState(q.toString());
    expect(Object.keys(s.filters)).toEqual(Array.from({ length: 8 }, (_, i) => `k0000${i}`));
    expect(encodePageState(s).length).toBeLessThanOrEqual(2000);
  });

  it("drops a 1 MB value and a 201-character value, and keeps 200", () => {
    const s = decodePageState(`f.big=${"x".repeat(1_000_000)}&f.ok=1&f.edge=${"y".repeat(201)}&f.fit=${"z".repeat(200)}`);
    expect(Object.keys(s.filters).sort()).toEqual(["fit", "ok"]);
  });

  it("drops the prototype keys", () => {
    const s = decodePageState("f.__proto__=x&f.constructor=y&f.prototype=z&f.a=1");
    expect(Object.keys(s.filters)).toEqual(["a"]);
    const hand = Object.create(null);
    hand.__proto__ = "x";
    hand.constructor = "y";
    expect(Object.keys(boundPageState({ selected: null, filters: hand, tab: null }).filters)).toEqual([]);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("refuses a control character in sel, and removes it from a value", () => {
    expect(decodePageState("sel=a%00b").selected).toBeNull();
    expect(decodePageState("sel=a%0Ab").selected).toBeNull();
    expect(decodePageState("f.a=x%00y%1Fz").filters.a).toBe("xyz");
    expect(decodePageState("f.a=%00%01").filters.a).toBeUndefined();
  });

  it("limits sel to 64 characters", () => {
    expect(decodePageState(`sel=${"a".repeat(64)}`).selected).toHaveLength(64);
    expect(decodePageState(`sel=${"a".repeat(65)}`).selected).toBeNull();
  });

  it("keeps a tab only when the page declares it", () => {
    expect(decodePageState("tab=two", page).tab).toBe("two");
    expect(decodePageState("tab=nope", page).tab).toBeNull();
    expect(decodePageState("tab=one", { tabs: [] }).tab).toBeNull();
    expect(decodePageState("tab=Bad%20Tab").tab).toBeNull();
  });

  it("keeps only tab and sel when the encoded string is over 2,000 characters", () => {
    const filters: Record<string, string> = {};
    for (let i = 0; i < 8; i++) filters[`k${i}`.padEnd(48, "n")] = "v".repeat(200);
    const s = boundPageState({ selected: "s", filters, tab: "one" }, page);
    expect(s.filters).toEqual({});
    expect(s.selected).toBe("s");
    expect(s.tab).toBe("one");
  });

  it("drops a filter name that is too long or not an id", () => {
    const s = decodePageState(`f.${"a".repeat(49)}=1&f.Bad=1&f.a-b=1&f.${"a".repeat(48)}=1`);
    expect(Object.keys(s.filters)).toEqual(["a".repeat(48)]);
  });
});

describe("decodePageState never throws", () => {
  it("gives the empty state for a bad value", () => {
    const boom = {
      entries: () => {
        throw new Error("x");
      },
    };
    for (const bad of [null, undefined, 5, {}, boom]) {
      expect(decodePageState(bad as unknown as string)).toEqual(EMPTY_PAGE_STATE);
    }
  });

  it("survives a broken percent sequence", () => {
    expect(() => decodePageState("sel=%E0%A4%A&tab=%")).not.toThrow();
  });
});
