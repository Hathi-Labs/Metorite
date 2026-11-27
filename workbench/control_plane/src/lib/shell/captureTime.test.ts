import { describe, expect, it } from "vitest";

import { captureToast, namedTimeToday, skippedTimeReason } from "./captureTime";

// The browser clock for every case: 10:00 on a Saturday, in the test zone.
const NOW = new Date(2026, 10, 7, 10, 0, 0);
const at = (h: number, m = 0) => new Date(2026, 10, 7, h, m, 0).getTime();

describe("namedTimeToday (CC-1)", () => {
  it("reads am and pm, with or without minutes", () => {
    expect(namedTimeToday("call the bank at 3 pm", NOW)).toEqual({ ms: at(15), said: "3 pm" });
    expect(namedTimeToday("call the bank at 3:30pm", NOW)).toEqual({ ms: at(15, 30), said: "3:30 pm" });
    expect(namedTimeToday("standup at 11 a.m.", NOW)?.ms).toBe(at(11));
  });
  it("reads a 24-hour clock and noon", () => {
    expect(namedTimeToday("review at 15:45", NOW)).toEqual({ ms: at(15, 45), said: "15:45" });
    expect(namedTimeToday("lunch at noon", NOW)).toEqual({ ms: at(12), said: "noon" });
  });
  it("names no time when the line has none", () => {
    expect(namedTimeToday("buy milk", NOW)).toBeNull();
    expect(namedTimeToday("room 3 pm", NOW)).toBeNull();
  });
  it("names no time of today when the time has passed", () => {
    expect(namedTimeToday("call at 9 am", NOW)).toBeNull();
    expect(namedTimeToday("call at 10 am", NOW)).toBeNull();
  });
  it("names no time of today when the line names another day", () => {
    expect(namedTimeToday("call the bank tomorrow at 3 pm", NOW)).toBeNull();
    expect(namedTimeToday("call on Monday at 3 pm", NOW)).toBeNull();
    expect(namedTimeToday("call on the 12th at 3 pm", NOW)).toBeNull();
  });
  it("refuses an hour that is not an hour", () => {
    expect(namedTimeToday("call at 13 pm", NOW)).toBeNull();
    expect(namedTimeToday("call at 0 pm", NOW)).toBeNull();
    expect(namedTimeToday("call at midnight", NOW)).toBeNull();
  });
  it("keeps a word like may as a plain word", () => {
    expect(namedTimeToday("I may call the bank at 3 pm", NOW)?.ms).toBe(at(15));
  });
  // Verifier P2, 2026-11-07: these named a time today, and the old day rule
  // dropped them.
  it("reads 'next steps' and a '.com' name as plain words, not as another day", () => {
    expect(namedTimeToday("Review next steps at 3 pm", NOW)?.ms).toBe(at(15));
    expect(namedTimeToday("Monday.com sync at 3 pm", NOW)?.ms).toBe(at(15));
    expect(namedTimeToday("call next week at 3 pm", NOW)).toBeNull();
    expect(namedTimeToday("call next Friday at 3 pm", NOW)).toBeNull();
  });
});

describe("skippedTimeReason (CC-1, verifier P2)", () => {
  it("names nothing for a line with no time", () => {
    expect(skippedTimeReason("buy milk")).toBeNull();
    expect(skippedTimeReason("room 3 pm")).toBeNull();
  });
  it("says why a timed line got no reminder", () => {
    expect(skippedTimeReason("call at 9 am")).toBe("That time has passed today.");
    expect(skippedTimeReason("call the bank tomorrow at 3 pm")).toBe("The line names another day. Plan it from your Inbox.");
    expect(skippedTimeReason("call at 3")).toBe("Add am or pm to set a reminder.");
    expect(skippedTimeReason("call at midnight")).toBe("Midnight names no time today.");
    expect(skippedTimeReason("call at 13 pm")).toBe("That is not a time of day.");
  });
});

describe("captureToast (CC-1)", () => {
  it("says the Inbox alone for a plain capture", () => {
    expect(captureToast({})).toEqual({ title: "Filed to your Inbox" });
  });
  it("says the reminder when one was made", () => {
    expect(captureToast({ reminder: { ms: at(15), said: "3 pm" } }).description).toBe("A reminder is set for 3 pm today.");
  });
  it("says why no reminder was made", () => {
    expect(captureToast({ reminderFailed: "Reminders are not available for you." }).description).toBe(
      "No reminder was set. Reminders are not available for you.",
    );
    expect(captureToast({ reminderSkipped: "That time has passed today." }).description).toBe(
      "No reminder was set. That time has passed today.",
    );
  });
});
