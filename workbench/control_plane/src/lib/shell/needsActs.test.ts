/**
 * The shared store of the needs feed's acts (`needsActs.ts`, NS-6 slice 6a).
 *
 * The store outlives a page, which a card's own state never did. These are
 * the four ways that could hide a live task or show a stale error, one test
 * each, and the reset on a new member.
 *
 * Mutations, run by hand (2026-10-10):
 *   - delete the `onClear(resetActs)` line: "a new member forgets every mark" fails;
 *   - drop the reset at the last release: "an error does not outlive the page" fails;
 *   - drop the hold window from `pruneActs`: "a task reopened elsewhere shows again" fails.
 */
import { afterEach, describe, expect, it } from "vitest";

import { clearAll } from "@/lib/dataCache";

import {
  ACT_HOLD_MS,
  EMPTY_ACTS,
  acquireReader,
  actsSnapshot,
  pruneActs,
  removeRow,
  resetActs,
  setRowError,
} from "./needsActs";

const releases: Array<() => void> = [];
const mount = () => {
  const release = acquireReader();
  releases.push(release);
  return release;
};

afterEach(() => {
  while (releases.length) releases.pop()!();
  resetActs();
});

describe("the shared acts store", () => {
  it("shares one answer between every reader", () => {
    mount();
    mount();
    removeRow("tasks:a", true, 0);
    setRowError("tasks:b", "Could not mark it done.", 0);
    const snap = actsSnapshot();
    expect([...snap.removed]).toEqual(["tasks:a"]);
    expect(snap.errors).toEqual({ "tasks:b": "Could not mark it done." });
    // The same object until something changes, as useSyncExternalStore needs.
    expect(actsSnapshot()).toBe(snap);
  });

  it("a new member forgets every mark (onClear)", () => {
    mount();
    removeRow("tasks:a", true);
    setRowError("tasks:b", "Could not mark it done.");
    clearAll(); // what `bindIdentity` runs on a change of member
    expect(actsSnapshot()).toBe(EMPTY_ACTS);
  });

  it("an error does not outlive the page: the last reader leaves, and it is gone", () => {
    const release = mount();
    setRowError("tasks:b", "Could not mark it done.");
    removeRow("tasks:a", true);
    release();
    mount(); // the page again, with no new act
    expect(actsSnapshot().errors).toEqual({});
    expect(actsSnapshot().removed.size).toBe(0);
  });

  it("an act that settles with no reader mounted writes nothing", () => {
    const release = mount();
    release();
    // A Done's failure arrives late, after the member left the page.
    setRowError("tasks:a", "Could not mark it done.");
    removeRow("tasks:a", true);
    mount();
    expect(actsSnapshot()).toBe(EMPTY_ACTS);
  });

  it("an answer that no longer holds a row forgets its mark and its error", () => {
    mount();
    removeRow("tasks:a", true, 0);
    setRowError("tasks:b", "Could not mark it done.", 0);
    pruneActs(["tasks:c"], 1_000);
    expect(actsSnapshot()).toBe(EMPTY_ACTS);
  });

  it("a task reopened elsewhere shows again once the hold window has passed", () => {
    mount();
    removeRow("tasks:a", true, 0);
    // An answer inside the window may predate the write: the row stays hidden.
    pruneActs(["tasks:a"], ACT_HOLD_MS - 1);
    expect(actsSnapshot().removed.has("tasks:a")).toBe(true);
    // An answer after it that still holds the task is the truth: it is live.
    pruneActs(["tasks:a"], ACT_HOLD_MS);
    expect(actsSnapshot().removed.has("tasks:a")).toBe(false);
  });

  it("an error lasts the same window, and no longer", () => {
    mount();
    setRowError("tasks:a", "Could not mark it done.", 0);
    pruneActs(["tasks:a"], 1_000);
    expect(actsSnapshot().errors).toEqual({ "tasks:a": "Could not mark it done." });
    pruneActs(["tasks:a"], ACT_HOLD_MS + 1);
    expect(actsSnapshot().errors).toEqual({});
  });

  it("a release that runs twice counts once", () => {
    const a = mount();
    mount();
    removeRow("tasks:a", true);
    a();
    a();
    expect(actsSnapshot().removed.has("tasks:a")).toBe(true);
  });
});
