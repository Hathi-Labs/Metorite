/**
 * The Projects rail's folds (`treeFold.ts`). Owner ask, 2026-10-10: start
 * closed, keep what is open while I work, forget it when I leave, when time
 * passes, or when I sign in again.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CLOSED,
  FOLD_AWAY_MS,
  foldSnapshot,
  forgetFold,
  hiddenAtTime,
  openFolds,
  resumed,
  toggleFold,
  toggled,
  withOpen,
} from "./treeFold";

const T0 = 1_000_000;

describe("the rules", () => {
  it("starts closed", () => {
    expect(CLOSED.open.size).toBe(0);
  });

  it("keeps what is open while the tab stays in view, however long", () => {
    // Review of #853: an idle clock closed the rail on the first chevron
    // click after half an hour of board work. In view is not away.
    const s = toggled(CLOSED, "space", "me");
    expect(resumed(s, T0 + 10 * FOLD_AWAY_MS, "me")).toBe(s);
  });

  it("keeps it after a short time away, and forgets it after a long one", () => {
    const away = hiddenAtTime(toggled(CLOSED, "space", "me"), T0);
    const back = resumed(away, T0 + FOLD_AWAY_MS - 1, "me");
    expect([...back.open]).toEqual(["space"]);
    expect(back.hiddenAt).toBeNull();
    expect(resumed(away, T0 + FOLD_AWAY_MS + 1, "me").open.size).toBe(0);
  });

  it("forgets when the folds belong to someone else", () => {
    const s = toggled(CLOSED, "space", "me");
    expect(resumed(s, T0 + 1, "someone-else").open.size).toBe(0);
  });

  it("toggles one row, and can force a side", () => {
    const opened = toggled(CLOSED, "a", "me");
    expect([...opened.open]).toEqual(["a"]);
    expect(toggled(opened, "a", "me").open.size).toBe(0);
    expect([...toggled(opened, "a", "me", true).open]).toEqual(["a"]);
  });

  it("opens a path and closes nothing, and returns the same state when nothing changes", () => {
    const s = toggled(CLOSED, "x", "me");
    const t = withOpen(s, ["a", "b"], "me");
    expect([...t.open].sort()).toEqual(["a", "b", "x"]);
    expect(withOpen(t, ["a"], "me")).toBe(t);
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
    // A pick opens its row in the click, not on a selection change.
    expect(tree).toMatch(/if \(children\.length > 0\) openFolds\(\[node\.id\]\);\s*onSelect\(node\);/);
  });

  it("a create, a move and a drop keep the node they touched in view", () => {
    // Review of #853: a child created under a closed row vanished on commit.
    const page = read("../page.tsx");
    expect(page).toMatch(/if \(creating\.parent\) openFolds\(\[creating\.parent\.id\]\);/);
    expect(page.match(/openFolds\(pathTo\(roots, (?:parentId|into)\)/g)).toHaveLength(2);
  });

  it("the Projects page forgets on leaving and resumes on return", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/forgetFold\(\)/);
    expect(page).toMatch(/"hidden"\) markHidden\(\);\s*else resumeFold\(\);/);
    expect(page).toMatch(/addEventListener\("visibilitychange"/);
  });

  it("is never written to browser storage (AGENTS.md rule 9)", () => {
    expect(read("./treeFold.ts")).not.toMatch(/(local|session)Storage\.|indexedDB/);
  });
});
