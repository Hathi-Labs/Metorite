// The choices of the time-zone select in the Calendar settings (RX-7).
//
// Mutation: drop the line that adds the saved zone, and "lists a saved zone the
// browser does not know" fails. Drop "UTC" from the first set, and "puts UTC
// first" fails.
import { describe, expect, it } from "vitest";

import { zoneHint, zoneLabel, zoneOptions } from "./zoneChoices";

const NOW = Date.parse("2026-11-02T12:00:00Z");

describe("zoneOptions", () => {
  it("puts UTC first and lists Kolkata with its offset", () => {
    const options = zoneOptions("UTC", NOW);
    expect(options[0].value).toBe("UTC");
    const kolkata = options.find((o) => o.value === "Asia/Kolkata" || o.value === "Asia/Calcutta");
    expect(kolkata?.hint).toBe("UTC+05:30");
  });

  it("lists a saved zone the browser does not know, so the value shows", () => {
    const options = zoneOptions("Mars/Olympus", NOW);
    expect(options.some((o) => o.value === "Mars/Olympus")).toBe(true);
  });

  it("lists each zone once", () => {
    const values = zoneOptions("Asia/Kolkata", NOW).map((o) => o.value);
    expect(new Set(values).size).toBe(values.length);
  });

  it("keeps the zone name in the keywords, so the filter finds it", () => {
    const [, second] = zoneOptions(null, NOW);
    expect(second.keywords).toBe(second.value);
  });
});

describe("zoneHint and zoneLabel", () => {
  it("reads the offset at the instant, with the clock change", () => {
    expect(zoneHint("America/New_York", Date.parse("2026-01-15T12:00:00Z"))).toBe("UTC-05:00");
    expect(zoneHint("America/New_York", Date.parse("2026-07-15T12:00:00Z"))).toBe("UTC-04:00");
  });

  it("gives no hint for a zone the browser does not know", () => {
    expect(zoneHint("Mars/Olympus", NOW)).toBe("");
  });

  it("writes the underscores as spaces", () => {
    expect(zoneLabel("America/New_York")).toBe("America/New York");
  });
});
