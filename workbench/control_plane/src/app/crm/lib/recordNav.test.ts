// `j` and `k` on the record page (WS-53 CRM-X4 item 5, spec section 19.8).
import { describe, expect, it } from "vitest";

import { navLabel, navStep, neighbour } from "./recordNav";

const ids = ["a", "b", "c"];

describe("neighbour", () => {
  it("gives the next id and the id before", () => {
    expect(neighbour(ids, "b", 1)).toBe("c");
    expect(neighbour(ids, "b", -1)).toBe("a");
  });

  it("gives null at each end", () => {
    expect(neighbour(ids, "c", 1)).toBeNull();
    expect(neighbour(ids, "a", -1)).toBeNull();
  });

  it("gives null for an id that the list does not hold, and for no list", () => {
    expect(neighbour(ids, "z", 1)).toBeNull();
    expect(neighbour(ids, "z", -1)).toBeNull();
    expect(neighbour([], "a", 1)).toBeNull();
  });
});

describe("navLabel", () => {
  it("says where the record sits in its list", () => {
    expect(navLabel(ids, "b")).toBe("2 of 3");
  });

  it("says nothing for an id that the list does not hold", () => {
    expect(navLabel(ids, "z")).toBeNull();
  });
});

describe("navStep", () => {
  const key = (over: Partial<Parameters<typeof navStep>[0]>) =>
    navStep({ key: "j", metaKey: false, ctrlKey: false, altKey: false, target: null, ...over });

  it("reads j as the next record and k as the one before", () => {
    expect(key({ key: "j" })).toBe(1);
    expect(key({ key: "k" })).toBe(-1);
  });

  it("does nothing with a modifier down, so Cmd-K stays the shell's", () => {
    expect(key({ key: "k", metaKey: true })).toBeNull();
    expect(key({ key: "k", ctrlKey: true })).toBeNull();
    expect(key({ key: "j", altKey: true })).toBeNull();
  });

  it("does nothing while the member types", () => {
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
      expect(key({ key: "j", target: { tagName } })).toBeNull();
    }
    expect(key({ key: "k", target: { tagName: "DIV", isContentEditable: true } })).toBeNull();
  });

  it("ignores every other key", () => {
    for (const other of ["J", "K", "ArrowDown", "Enter", "l"]) {
      expect(key({ key: other })).toBeNull();
    }
  });
});
