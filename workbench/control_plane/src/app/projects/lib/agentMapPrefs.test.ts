import { describe, expect, it } from "vitest";

import { DEFAULT_PREFS, prefsKey, readPrefs, writePrefs } from "./agentMapPrefs";

/** A `localStorage` that lives in a variable: this runner has no DOM. */
const store = (held: Record<string, string> = {}) => ({
  getItem: (key: string) => held[key] ?? null,
  setItem: (key: string, value: string) => {
    held[key] = value;
  },
});
const blocked = {
  getItem: (): string | null => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
};

describe("the remembered filters of the Agents map (decision G5)", () => {
  it("gives the defaults for an empty store", () => {
    expect(readPrefs("ana@x.test", store())).toEqual(DEFAULT_PREFS);
    expect(DEFAULT_PREFS).toEqual({ person: "", mode: "active" });
  });
  it("keeps People and Active or All for each member", () => {
    const s = store();
    writePrefs("Ana@x.test", { person: "me", mode: "all" }, s);
    expect(readPrefs("ana@x.test", s)).toEqual({ person: "me", mode: "all" });
    expect(readPrefs("ben@x.test", s)).toEqual(DEFAULT_PREFS);
  });
  it("reads a bad value as the default", () => {
    for (const text of ["not json", "7", "null", '{"person":4,"mode":"tiles"}']) {
      expect(readPrefs("a@x.test", store({ [prefsKey("a@x.test")]: text }))).toEqual(DEFAULT_PREFS);
    }
  });
  it("works with a store that the browser blocks", () => {
    expect(readPrefs("a@x.test", blocked)).toEqual(DEFAULT_PREFS);
    expect(() => writePrefs("a@x.test", DEFAULT_PREFS, blocked)).not.toThrow();
  });
});
