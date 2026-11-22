/**
 * WS-62 CF-4b: the brief row is told apart by its id, so the starter pills
 * show under the day's brief and under no other lone assistant row.
 */
import { describe, expect, it } from "vitest";

import { holdsOnlyBrief, isBriefRow } from "./briefTurn";

const brief = { id: "brief-2026-11-03", role: "assistant" };

describe("isBriefRow", () => {
  it("takes the id the gateway writes, and nothing else", () => {
    expect(isBriefRow("brief-2026-11-03")).toBe(true);
    expect(isBriefRow("brief-2026-11-3")).toBe(false);
    expect(isBriefRow("brief-2026-11-03-x")).toBe(false);
    expect(isBriefRow("reminder-2026-11-03")).toBe(false);
    expect(isBriefRow("m-1")).toBe(false);
    expect(isBriefRow(undefined)).toBe(false);
    expect(isBriefRow(42)).toBe(false);
  });
});

describe("holdsOnlyBrief", () => {
  it("is true for a chat that holds only the brief", () => {
    expect(holdsOnlyBrief([brief])).toBe(true);
  });

  it("is false for a chat a reminder opens, and any other lone assistant row", () => {
    expect(holdsOnlyBrief([{ id: "rem-7f3a", role: "assistant" }])).toBe(false);
    expect(holdsOnlyBrief([{ id: "a1", role: "assistant" }])).toBe(false);
    expect(holdsOnlyBrief([{ id: "brief-2026-11-03", role: "system" }])).toBe(false);
  });

  it("is false for an empty chat, and for a chat with a member message", () => {
    expect(holdsOnlyBrief([])).toBe(false);
    expect(holdsOnlyBrief([brief, { id: "u1", role: "user" }])).toBe(false);
    expect(holdsOnlyBrief([{ id: "u1", role: "user" }])).toBe(false);
  });
});
