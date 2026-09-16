import { describe, expect, it } from "vitest";

import { findMerges, mergeWarning } from "./statusSwitch";

const rows = [
  { status_id: "a", name: "Next up" },
  { status_id: "b", name: "Parked" },
  { status_id: "c", name: "Doing" },
];

describe("findMerges", () => {
  it("reports two lanes landing in one", () => {
    // The measured case: both are `todo`, the parent has one `todo` lane.
    const merges = findMerges(rows, { a: "Waiting", b: "Waiting", c: "In progress" });
    expect(merges).toEqual([["Waiting", ["Next up", "Parked"]]]);
  });

  it("says nothing when every lane keeps its own target", () => {
    expect(
      findMerges(rows, { a: "To do", b: "Backlog", c: "In progress" })
    ).toEqual([]);
  });

  it("follows the HUMAN's choice, not the suggestion", () => {
    // ⚠️ The property that makes the warning trustworthy. Re-pointing one row
    // by hand resolves the merge, and the card must stop warning about it —
    // a warning that outlives the thing it warns about teaches people to
    // click past warnings.
    const merged = findMerges(rows, { a: "Waiting", b: "Waiting" });
    expect(merged).toHaveLength(1);
    const fixed = findMerges(rows, { a: "Waiting", b: "Backlog" });
    expect(fixed).toEqual([]);
  });

  it("creates a warning when the human MAKES a merge by hand", () => {
    // The other direction, and it is not the same test: the automatic rule
    // could have produced no merge at all, and a person is still free to point
    // two rows at one lane.
    expect(findMerges(rows, { a: "Doing", b: "Doing" })).toEqual([
      ["Doing", ["Next up", "Parked"]],
    ]);
  });

  it("does not group UNANSWERED rows together", () => {
    // Every blank shares the value "", so a naive group would report every
    // unanswered row as merging with every other one — a warning on a card
    // nobody has filled in yet.
    expect(findMerges(rows, {})).toEqual([]);
    expect(findMerges(rows, { c: "Doing" })).toEqual([]);
  });

  it("reports three lanes landing in one as ONE merge", () => {
    const merges = findMerges(
      [...rows, { status_id: "d", name: "Later" }],
      { a: "Waiting", b: "Waiting", d: "Waiting" }
    );
    expect(merges).toEqual([["Waiting", ["Next up", "Parked", "Later"]]]);
  });
});

describe("mergeWarning", () => {
  it("is empty when nothing merges, so the card renders no banner", () => {
    expect(mergeWarning([])).toBe("");
  });

  it("names the lanes and says the switch back will not undo it", () => {
    const said = mergeWarning([["Waiting", ["Next up", "Parked"]]]);
    expect(said).toContain("Next up and Parked both become Waiting.");
    expect(said).toContain("Switching back will not separate them again.");
  });
});
