/**
 * The Projects rail's folds (`railFold.ts`). Owner ask, 2026-10-10: start
 * closed, keep what is open while I work, forget it when I leave, when time
 * passes, or when I sign in again.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CLOSED,
  FOLD_IDLE_MS,
  foldSnapshot,
  forgetFold,
  openFolds,
  resumed,
  toggleFold,
  toggled,
  withOpen,
} from "./railFold";

const T0 = 1_000_000;

describe("the rules", () => {
  it("starts closed", () => {
    expect(CLOSED.open.size).toBe(0);
  });

  it("keeps what is open while the member is active", () => {
    const s = toggled(CLOSED, "space", T0, "me");
    expect(resumed(s, T0 + FOLD_IDLE_MS - 1, "me")).toBe(s);
  });

  it("forgets after the idle window", () => {
    const s = toggled(CLOSED, "space", T0, "me");
    expect(resumed(s, T0 + FOLD_IDLE_MS + 1, "me").open.size).toBe(0);
  });

  it("forgets when the folds belong to someone else", () => {
    const s = toggled(CLOSED, "space", T0, "me");
    expect(resumed(s, T0 + 1, "someone-else").open.size).toBe(0);
  });

  it("toggles one row, and can force a side", () => {
    const opened = toggled(CLOSED, "a", T0, "me");
    expect([...opened.open]).toEqual(["a"]);
    expect(toggled(opened, "a", T0, "me").open.size).toBe(0);
    expect([...toggled(opened, "a", T0, "me", true).open]).toEqual(["a"]);
  });

  it("opens a path and closes nothing, and returns the same state when nothing changes", () => {
    const s = toggled(CLOSED, "x", T0, "me");
    const t = withOpen(s, ["a", "b"], T0 + 5, "me");
    expect([...t.open].sort()).toEqual(["a", "b", "x"]);
    expect(t.touchedAt).toBe(T0 + 5);
    expect(withOpen(t, ["a"], T0 + 9, "me")).toBe(t);
  });
});

describe("the store", () => {
  it("is forgotten as a whole when the member leaves the app", () => {
    toggleFold("space");
    openFolds(["folder"]);
    expect([...foldSnapshot().open].sort()).toEqual(["folder", "space"]);
    forgetFold();
    expect(foldSnapshot().open.size).toBe(0);
  });
});

describe("the wiring (source fence)", () => {
  const read = (rel: string) =>
    readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");

  it("a tree row reads the store, and no longer holds its own fold", () => {
    const tree = read("../components/ProjectTree.tsx");
    expect(tree).toContain("useFoldOpen(node.id)");
    expect(tree).not.toMatch(/useState\(depth < 1\)/);
  });

  it("the Projects page forgets on leaving and resumes on return", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/forgetFold\(\)/);
    expect(page).toMatch(/resumeFold\(\)[\s\S]{0,300}"visibilitychange"/);
  });

  it("is never written to browser storage (AGENTS.md rule 9)", () => {
    expect(read("./railFold.ts")).not.toMatch(/(local|session)Storage\.|indexedDB/);
  });
});
