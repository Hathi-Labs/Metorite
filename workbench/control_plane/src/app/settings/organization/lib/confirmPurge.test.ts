import { describe, expect, it } from "vitest";
import { purgeConfirmed } from "./confirmPurge";

describe("purgeConfirmed", () => {
  it("arms the button only on the member's own address", () => {
    expect(purgeConfirmed("priya@fracktal.in", "priya@fracktal.in")).toBe(true);
    expect(purgeConfirmed("owner@fracktal.in", "priya@fracktal.in")).toBe(false);
  });

  it("refuses anything short of the whole address", () => {
    // The point of the gate is that the admin reads and re-types it. A
    // prefix, a suffix, or the local part alone is somebody typing fast.
    for (const typed of ["priya", "priya@", "@fracktal.in", "priya@fracktal"]) {
      expect(purgeConfirmed(typed, "priya@fracktal.in")).toBe(false);
    }
  });

  it("is not a spelling test — casing and copy-paste whitespace pass", () => {
    expect(purgeConfirmed("Priya@Fracktal.IN", "priya@fracktal.in")).toBe(true);
    expect(purgeConfirmed("  priya@fracktal.in  ", "priya@fracktal.in")).toBe(
      true
    );
    expect(purgeConfirmed("priya@fracktal.in", "PRIYA@FRACKTAL.IN")).toBe(true);
  });

  it("never confirms an empty input", () => {
    // The default state of the dialog. If this were ever true the gate would
    // be absent rather than weak, since the button is armed on first paint.
    expect(purgeConfirmed("", "priya@fracktal.in")).toBe(false);
    expect(purgeConfirmed("   ", "priya@fracktal.in")).toBe(false);
  });

  it("treats a member with no address as unconfirmable, not as always-confirmed", () => {
    // `typed === member.email` would make an empty box match an empty address.
    // Nobody is not everybody — the same rule `isSelf` carries.
    expect(purgeConfirmed("", "")).toBe(false);
    expect(purgeConfirmed("   ", "")).toBe(false);
    expect(purgeConfirmed("priya@fracktal.in", "")).toBe(false);
  });
});
