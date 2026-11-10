/**
 * `OptionList` (WS-61 KB-2b): the driver moves the active row with
 * `stepActive`, and each row's id derives from the list's id.
 */
import { describe, expect, it } from "vitest";

import { optionId, stepActive } from "./OptionList";

describe("stepActive", () => {
  it("moves one row and wraps at both ends", () => {
    expect(stepActive(0, 1, 3)).toBe(1);
    expect(stepActive(2, 1, 3)).toBe(0);
    expect(stepActive(0, -1, 3)).toBe(2);
  });

  it("stays at 0 for an empty list", () => {
    expect(stepActive(0, 1, 0)).toBe(0);
    expect(stepActive(4, -1, 0)).toBe(0);
  });
});

describe("optionId", () => {
  it("names each row from the list", () => {
    expect(optionId("kb-slash-menu", 2)).toBe("kb-slash-menu-option-2");
  });
});
