import { describe, expect, it } from "vitest";

import { NAME_MAX, nameProblem, normaliseName } from "../OrgNameForm";

describe("organization name", () => {
  it("trims and collapses inner whitespace like the gateway does", () => {
    expect(normaliseName("  Fracktal \n  Works ")).toBe("Fracktal Works");
  });

  it("refuses a blank name", () => {
    expect(nameProblem("   ")).toMatch(/cannot be empty/);
  });

  it("accepts the limit and refuses one past it", () => {
    expect(nameProblem("a".repeat(NAME_MAX))).toBeNull();
    expect(nameProblem("a".repeat(NAME_MAX + 1))).toMatch(/limit/);
  });
});
