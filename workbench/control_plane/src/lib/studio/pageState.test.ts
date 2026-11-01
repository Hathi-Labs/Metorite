import { describe, expect, it } from "vitest";

import { EMPTY_PAGE_STATE, decodePageState, encodePageState } from "./pageState";

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
