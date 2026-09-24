/**
 * /tasks · board drop refusals.
 *
 * What is pinned: the refusal set is exactly the drops the board's handlers
 * ignore — same-column under a field sort (the sort owns the order) and
 * select mode — and everything else stays a real drop, especially the
 * cross-stage re-file under a field sort.
 */

import { describe, expect, it } from "vitest";

import { dropRefusal } from "./dropRules";

describe("dropRefusal", () => {
  it("allows every drop in Manual sort", () => {
    expect(
      dropRefusal({ selectMode: false, sortField: "manual", sameColumn: true }),
    ).toBeNull();
    expect(
      dropRefusal({ selectMode: false, sortField: "manual", sameColumn: false }),
    ).toBeNull();
  });

  it("still allows a cross-stage re-file under a field sort", () => {
    expect(
      dropRefusal({ selectMode: false, sortField: "priority", sameColumn: false }),
    ).toBeNull();
  });

  it("refuses a same-column drop under a field sort, naming the sort", () => {
    const reason = dropRefusal({
      selectMode: false,
      sortField: "priority",
      sameColumn: true,
    });
    expect(reason).toContain("Priority");
    expect(reason).toContain("Manual");
  });

  it("names whichever sort is active, from the toolbar's own label map", () => {
    expect(
      dropRefusal({ selectMode: false, sortField: "due", sameColumn: true }),
    ).toContain("Due date");
  });

  it("refuses everything in select mode", () => {
    expect(
      dropRefusal({ selectMode: true, sortField: "manual", sameColumn: false }),
    ).toContain("select mode");
  });
});
