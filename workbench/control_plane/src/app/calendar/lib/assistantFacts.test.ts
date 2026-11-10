/** TC-1 (D172.4): Calendar tells the one assistant which task card is open. */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { calendarAssistantFacts } from "./assistantFacts";

describe("Calendar assistant facts", () => {
  it("names the open task with its id, and the chip shows its title", () => {
    const f = calendarAssistantFacts({ openTask: { id: "i-7", title: "Send the quote" } });
    const text = f.facts.join("\n");
    expect(text).toContain("Send the quote");
    expect(text).toContain("item_id: i-7");
    expect(text).toContain('"This task" means it.');
    expect(f.label).toBe("Send the quote");
  });

  it("names only the page when no card is open", () => {
    const f = calendarAssistantFacts({ openTask: null });
    expect(f.facts).toEqual(["They have the Calendar page open."]);
    expect(f.label).toBeUndefined();
  });

  it("cuts a long title for the chip", () => {
    expect(calendarAssistantFacts({ openTask: { id: "x", title: "w".repeat(80) } }).label).toHaveLength(40);
  });

  it("is published by the Calendar page", () => {
    const page = readFileSync(new URL("../page.tsx", import.meta.url), "utf-8");
    expect(page).toContain("useAssistantFacts(");
    expect(page).toContain("calendarAssistantFacts(");
  });
});
