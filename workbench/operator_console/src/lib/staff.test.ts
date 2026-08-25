import { describe, it, expect } from "vitest";
import {
  isStaff,
  requireStaff,
  staffSecret,
  StaffForbidden,
  StaffUnconfigured,
} from "./staff";

const ENV = { OPERATOR_CONSOLE_STAFF_SECRET: "staff-secret" };

describe("the interim staff gate", () => {
  it("fails CLOSED when the secret is unset (refuses everyone)", () => {
    expect(() => isStaff("anything", {})).toThrow(StaffUnconfigured);
    expect(() => requireStaff("anything", {})).toThrow(StaffUnconfigured);
  });

  it("admits the correct secret", () => {
    expect(isStaff("staff-secret", ENV)).toBe(true);
    expect(() => requireStaff("staff-secret", ENV)).not.toThrow();
  });

  it("refuses a wrong secret", () => {
    expect(isStaff("wrong", ENV)).toBe(false);
    expect(() => requireStaff("wrong", ENV)).toThrow(StaffForbidden);
  });

  it("refuses a missing/blank secret", () => {
    expect(isStaff(null, ENV)).toBe(false);
    expect(isStaff("", ENV)).toBe(false);
    expect(isStaff("   ", ENV)).toBe(false);
  });

  it("a length mismatch is rejected (constant-time compare guard)", () => {
    expect(isStaff("staff-secret-longer", ENV)).toBe(false);
    expect(isStaff("staff", ENV)).toBe(false);
  });

  it("reads the secret from the environment, trimmed", () => {
    expect(staffSecret({ OPERATOR_CONSOLE_STAFF_SECRET: "  s  " })).toBe("s");
    expect(staffSecret({})).toBeUndefined();
  });
});
