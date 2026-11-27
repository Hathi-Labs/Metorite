import { describe, expect, it } from "vitest";

import { shouldCloseOnEscape } from "./TaskFocusModal";

const inside = { contains: (n: unknown) => n === "in" };
const field = (tagName: string) => ({ tagName });

describe("TaskFocusModal Escape (TC-1)", () => {
  it("closes on a plain Escape", () => {
    expect(shouldCloseOnEscape({ key: "Escape", defaultPrevented: false, target: null }, inside)).toBe(true);
  });
  it("ignores an Escape another handler used", () => {
    expect(shouldCloseOnEscape({ key: "Escape", defaultPrevented: true, target: null }, inside)).toBe(false);
  });
  it("ignores an Escape typed in a text field outside the card", () => {
    const target = field("TEXTAREA") as unknown as EventTarget;
    expect(shouldCloseOnEscape({ key: "Escape", defaultPrevented: false, target }, inside)).toBe(false);
  });
  it("closes on an Escape typed in a field inside the card", () => {
    const target = Object.assign(field("INPUT"), { toString: () => "in" });
    const card = { contains: () => true };
    expect(shouldCloseOnEscape({ key: "Escape", defaultPrevented: false, target: target as unknown as EventTarget }, card)).toBe(true);
  });
  it("ignores other keys", () => {
    expect(shouldCloseOnEscape({ key: "a", defaultPrevented: false, target: null }, inside)).toBe(false);
  });
});
