/**
 * Projects · per-row pending and per-row error (WS-27bd item 2).
 *
 * The case the whole item exists for is the first one: three operations in
 * flight at once produce three independent spinners, and when one of them
 * fails the message is attached to THAT row and the other two are unaffected.
 * A single `isPending`/`error` pair passes every test you would write about one
 * row and fails this one.
 */

import { describe, expect, it } from "vitest";

import {
  NO_ROWS,
  type RowEvent,
  type RowState,
  anyPending,
  errorFor,
  isPending,
  rowsReducer,
} from "./rowState";

const run = (events: RowEvent[], from: RowState = NO_ROWS): RowState =>
  events.reduce(rowsReducer, from);

describe("three at once", () => {
  const inFlight = run([
    { kind: "start", id: "a" },
    { kind: "start", id: "b" },
    { kind: "start", id: "c" },
  ]);

  it("three concurrent operations are three independent pendings", () => {
    expect([...inFlight.pending].sort()).toEqual(["a", "b", "c"]);
    for (const id of ["a", "b", "c"]) expect(isPending(inFlight, id)).toBe(true);
    expect(isPending(inFlight, "d")).toBe(false);
  });

  it("one failure is attributed to one row, and stops only that spinner", () => {
    const after = rowsReducer(inFlight, {
      kind: "failed",
      id: "b",
      message: "That link would make a loop.",
    });

    expect(errorFor(after, "b")).toBe("That link would make a loop.");
    expect(isPending(after, "b")).toBe(false);

    // The other two are untouched — still spinning, still blameless. This is
    // the assertion a single `isPending`/`error` pair cannot satisfy.
    expect(isPending(after, "a")).toBe(true);
    expect(isPending(after, "c")).toBe(true);
    expect(errorFor(after, "a")).toBeNull();
    expect(errorFor(after, "c")).toBeNull();
    expect([...after.errors.keys()]).toEqual(["b"]);
  });

  it("the survivors settle independently of the failure", () => {
    const after = run(
      [
        { kind: "failed", id: "b", message: "nope" },
        { kind: "settled", id: "a" },
        { kind: "settled", id: "c" },
      ],
      inFlight,
    );
    expect(anyPending(after)).toBe(false);
    expect([...after.errors]).toEqual([["b", "nope"]]);
  });

  it("two rows can fail with two different messages", () => {
    const after = run(
      [
        { kind: "failed", id: "a", message: "first" },
        { kind: "failed", id: "c", message: "second" },
      ],
      inFlight,
    );
    expect(errorFor(after, "a")).toBe("first");
    expect(errorFor(after, "c")).toBe("second");
  });
});

describe("a retry", () => {
  const failed = run([
    { kind: "start", id: "a" },
    { kind: "failed", id: "a", message: "the gateway refused" },
  ]);

  it("does NOT blank the message while it is in flight", () => {
    // Deliberate: clearing on entry hides the failure at the moment it is
    // being read, and
    // the row would show a spinner over an explanation-shaped hole.
    const retrying = rowsReducer(failed, { kind: "start", id: "a" });
    expect(isPending(retrying, "a")).toBe(true);
    expect(errorFor(retrying, "a")).toBe("the gateway refused");
  });

  it("clears it on success", () => {
    const ok = run([{ kind: "start", id: "a" }, { kind: "settled", id: "a" }], failed);
    expect(errorFor(ok, "a")).toBeNull();
    expect(isPending(ok, "a")).toBe(false);
  });

  it("replaces it when the retry fails differently", () => {
    const again = run(
      [{ kind: "start", id: "a" }, { kind: "failed", id: "a", message: "still no" }],
      failed,
    );
    expect(errorFor(again, "a")).toBe("still no");
    expect(again.errors.size).toBe(1);
  });

  it("can be dismissed without touching anybody else", () => {
    const two = run([{ kind: "failed", id: "b", message: "other" }], failed);
    const after = rowsReducer(two, { kind: "dismiss", id: "a" });
    expect(errorFor(after, "a")).toBeNull();
    expect(errorFor(after, "b")).toBe("other");
  });
});

describe("rows that have gone away", () => {
  const messy = run([
    { kind: "start", id: "a" },
    { kind: "start", id: "gone" },
    { kind: "failed", id: "alsogone", message: "about a row nobody renders" },
  ]);

  it("prune drops state keyed to a row the list no longer has", () => {
    const after = rowsReducer(messy, { kind: "prune", ids: ["a"] });
    expect([...after.pending]).toEqual(["a"]);
    expect(after.errors.size).toBe(0);
  });

  it("prune keeps the rows that are still there", () => {
    const after = rowsReducer(messy, { kind: "prune", ids: ["a", "gone", "alsogone"] });
    expect([...after.pending].sort()).toEqual(["a", "gone"]);
    expect(errorFor(after, "alsogone")).toBe("about a row nobody renders");
  });

  it("pruning nothing away returns the same object", () => {
    // Identity, not equality: a component prunes from an effect on every load,
    // and a fresh object every time is a render loop.
    const same = rowsReducer(messy, { kind: "prune", ids: ["a", "gone", "alsogone"] });
    expect(same).toBe(messy);
  });
});

describe("the reducer is pure", () => {
  it("no event mutates the state it was given", () => {
    const before = run([
      { kind: "start", id: "a" },
      { kind: "failed", id: "b", message: "x" },
    ]);
    const pending = [...before.pending];
    const errors = [...before.errors];

    for (const event of [
      { kind: "start", id: "z" },
      { kind: "settled", id: "a" },
      { kind: "failed", id: "a", message: "y" },
      { kind: "dismiss", id: "b" },
      { kind: "prune", ids: [] },
    ] satisfies RowEvent[]) {
      rowsReducer(before, event);
    }

    expect([...before.pending]).toEqual(pending);
    expect([...before.errors]).toEqual(errors);
  });

  it("a no-op event returns the same object", () => {
    const state = run([{ kind: "start", id: "a" }]);
    expect(rowsReducer(state, { kind: "start", id: "a" })).toBe(state);
    expect(rowsReducer(state, { kind: "settled", id: "b" })).toBe(state);
    expect(rowsReducer(state, { kind: "dismiss", id: "b" })).toBe(state);
  });

  it("NO_ROWS is empty and stays empty", () => {
    expect(anyPending(NO_ROWS)).toBe(false);
    expect(NO_ROWS.errors.size).toBe(0);
    rowsReducer(NO_ROWS, { kind: "start", id: "a" });
    expect(NO_ROWS.pending.size).toBe(0);
  });
});
