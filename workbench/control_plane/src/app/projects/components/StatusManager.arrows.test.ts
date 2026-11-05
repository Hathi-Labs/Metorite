import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Task 1792. The Statuses dialog shows its order controls in plain sight.
// A source pin, as the other Projects component fences are: the labels are the
// contract a screen reader and the chat parity check rely on.
const source = readFileSync(join(__dirname, "StatusManager.tsx"), "utf-8");

describe("Statuses dialog order controls", () => {
  it("draws a visible Move up and Move down button on each row", () => {
    expect(source).toContain("aria-label={`Move ${status.name} up`}");
    expect(source).toContain("aria-label={`Move ${status.name} down`}");
  });

  it("disables the arrow at the end of its stage group", () => {
    expect(source).toContain("index === 0");
    expect(source).toContain("index === group.rows.length - 1");
  });

  it("uses the existing Button and the reorder() the menu uses", () => {
    expect(source).toMatch(/icon="ChevronUp"/);
    expect(source).toMatch(/icon="ChevronDown"/);
    expect(source).toContain('move(status, "up")');
    expect(source).toContain('move(status, "down")');
  });
});
