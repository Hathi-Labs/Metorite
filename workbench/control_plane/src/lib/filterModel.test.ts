// Fence for the shared filter model (STU-AF4, task 3152).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ANY,
  SEARCH_DELAY_MS,
  searchBoxStart,
  searchBoxSync,
  searchBoxWrote,
  searchChanges,
  searchCommitter,
  setCustom,
  setRange,
} from "./filterModel";

const MOVED = [
  "searchBoxStart",
  "searchBoxSync",
  "searchBoxWrote",
  "searchChanges",
  "searchCommitter",
  "setCustom",
  "setRange",
];

describe("the CRM imports the shared model", () => {
  const src = readFileSync(join(__dirname, "../app/crm/lib/filterBar.ts"), "utf-8");
  const reexport = /export\s*\{([^}]*)\}\s*from\s*"@\/lib\/filterModel"/.exec(src);
  const exported = (reexport?.[1] ?? "").split(",").map((n) => n.trim()).filter(Boolean);
  it("re-exports every moved name from @/lib/filterModel", () => {
    expect(reexport).not.toBeNull();
    for (const name of ["ANY", "SEARCH_DELAY_MS", ...MOVED]) expect(exported).toContain(name);
  });
  it("re-exports the SearchBoxState type from @/lib/filterModel", () => {
    expect(src).toMatch(/export\s+type\s*\{\s*SearchBoxState\s*\}\s*from\s*"@\/lib\/filterModel"/);
  });
  it("imports the names it uses itself", () => {
    const imp = /import\s*\{([^}]*)\}\s*from\s*"@\/lib\/filterModel"/.exec(src);
    expect(imp).not.toBeNull();
    const names = (imp?.[1] ?? "").split(",").map((n) => n.trim());
    for (const name of ["ANY", "setCustom", "setRange"]) expect(names).toContain(name);
  });
  it("defines none of the moved names itself", () => {
    for (const name of ["ANY", "SEARCH_DELAY_MS", ...MOVED]) {
      expect(src).not.toMatch(new RegExp(String.raw`export\s+(function|const|let|class)\s+${name}\b`));
    }
    expect(src).not.toMatch(/(export\s+)?(type|interface)\s+SearchBoxState\s*[={]/);
  });
});

describe("search box state machine", () => {
  it("starts and syncs", () => {
    const s = searchBoxStart("a");
    expect(s).toEqual({ draft: "a", seen: "a", mine: null });
    expect(searchBoxSync(s, "a")).toBe(s);
    expect(searchBoxSync(s, "b")).toEqual({ draft: "b", seen: "b", mine: null });
  });
  it("keeps the draft after its own write, trimmed", () => {
    const w = searchBoxWrote(searchBoxStart(""), "printer ");
    expect(w.mine).toBe("printer");
    expect(searchBoxSync({ ...w, draft: "printer order" }, "printer").draft).toBe("printer order");
  });
  it("compares trimmed text", () => {
    expect(searchChanges(" a ", "a")).toBe(false);
    expect(searchChanges("b", "a")).toBe(true);
  });
});

describe("searchCommitter", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it("commits the last text once after the delay", () => {
    const commit = vi.fn();
    const c = searchCommitter(commit);
    c.type("a");
    c.type("ab");
    vi.advanceTimersByTime(SEARCH_DELAY_MS - 1);
    expect(commit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith("ab");
  });
  it("cancel stops the commit", () => {
    const commit = vi.fn();
    const c = searchCommitter(commit, 50);
    c.type("x");
    c.cancel();
    vi.advanceTimersByTime(100);
    expect(commit).not.toHaveBeenCalled();
  });
});

describe("setters", () => {
  const view = { custom: { Region: ["N"] } as Record<string, string[]>, ranges: {} as Partial<Record<"a_from", string>>, k: 1 };
  it("setCustom sets, clears and keeps other keys", () => {
    const set = setCustom(view, "Tier", "Gold");
    expect(set.custom).toEqual({ Region: ["N"], Tier: ["Gold"] });
    expect(setCustom(set, "Region", ANY).custom).toEqual({ Tier: ["Gold"] });
    expect(view.custom).toEqual({ Region: ["N"] });
    expect(set.k).toBe(1);
  });
  it("setRange accepts a day and clears anything else", () => {
    const set = setRange(view, "a_from", "2026-11-14");
    expect(set.ranges).toEqual({ a_from: "2026-11-14" });
    expect(setRange(set, "a_from", "nope").ranges).toEqual({});
  });
});
