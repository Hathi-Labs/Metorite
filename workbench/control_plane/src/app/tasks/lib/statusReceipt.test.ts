/**
 * D79 — a status write names the status and the project.
 *
 * This file OWNS that rule (R7). The receipt is the undo toast's title, so
 * these are the words a member reads after every status write in My Tasks.
 */
import { describe, expect, it } from "vitest";

import { doneReceipt, moveReceipt } from "./statusReceipt";
import { myListLabel } from "./utils";

const BOARD = { projectName: "Website relaunch", personal: false };
const MINE = { projectName: "My Tasks", personal: true };

describe("a status write names the status and the project", () => {
  it("names both for a board task", () => {
    expect(moveReceipt({ name: "In review" }, BOARD)).toBe(
      "Moved to In review · Website relaunch",
    );
  });

  it("names only the status for a personal task", () => {
    expect(moveReceipt({ name: "Doing" }, MINE)).toBe("Moved to Doing");
  });

  it("names only the status when the project is unknown", () => {
    expect(moveReceipt({ name: "Doing" }, { personal: false })).toBe("Moved to Doing");
  });
});

describe("Mark done names the Done status it chose", () => {
  const TWO = [{ name: "Shipped" }, { name: "Done" }];

  it("names the first Done status and the project when there are two", () => {
    expect(doneReceipt(TWO, BOARD)).toBe("Done · Shipped in Website relaunch");
  });

  it("names the status alone for a personal task", () => {
    expect(doneReceipt(TWO, MINE)).toBe("Done · Shipped");
  });

  it("says only Marked done when there is one Done status to choose", () => {
    expect(doneReceipt([{ name: "Done" }], BOARD)).toBe("Marked done");
    expect(doneReceipt([], BOARD)).toBe("Marked done");
  });
});

describe("the detail header chip is my list, not a status", () => {
  it("reads My list: Next action, so it cannot pass for a second status", () => {
    expect(myListLabel("NEXT")).toBe("My list: Next action");
    expect(myListLabel("DONE")).toBe("My list: Done");
  });
});
