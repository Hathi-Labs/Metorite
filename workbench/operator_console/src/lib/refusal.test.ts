// A refusal reads as a sentence, and never as a blank cell.
//
// 🔴 This renders on the page an operator opens when something is ALREADY
// broken. A throw there replaces the evidence with an error boundary, and an
// empty string reads as "nothing happened" — which is the conclusion that
// cost the owner several days on a tier save that never landed.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { refusalTitle } from "./refusal";

const SRC = join(__dirname, "..");
const FEED = readFileSync(join(SRC, "app", "activity", "ActivityFeed.tsx"), "utf8");

/** The exact shape the Console writes. */
const REAL = {
  method: "POST",
  path: "/catalog/bindings",
  status: 400,
  why: "'openai/gpt-4o' declares no capability for task 'chat'; declare it first",
};

describe("refusalTitle", () => {
  it("🔴 renders the refusal that actually stopped the owner", () => {
    expect(refusalTitle(REAL)).toBe(
      "POST /catalog/bindings answered 400: 'openai/gpt-4o' declares no " +
        "capability for task 'chat'; declare it first",
    );
  });

  it("names the act even when the reason is missing", () => {
    expect(refusalTitle({ method: "POST", path: "/keys", status: 403 })).toBe(
      "POST /keys answered 403.",
    );
  });

  it("still says something when only the reason survived", () => {
    expect(refusalTitle({ why: "Forbidden" })).toBe("A write was refused: Forbidden");
  });

  it("⚠️ never returns an empty string, whatever it is handed", () => {
    // An empty cell on this page reads as "no problem here".
    for (const junk of [null, undefined, 42, "text", [], {}, { why: "" }]) {
      const out = refusalTitle(junk);
      expect(typeof out, JSON.stringify(junk)).toBe("string");
      expect(out.length, JSON.stringify(junk)).toBeGreaterThan(0);
    }
  });

  it("⚠️ never throws on a row written by an OLDER Console", () => {
    // The trail is history. A row predating any field here must still render.
    for (const junk of [
      { method: 1, path: 2, status: {}, why: [] },
      { status: "400" },
      { path: "/x" },
    ]) {
      expect(() => refusalTitle(junk)).not.toThrow();
    }
  });

  it("takes a numeric status and a string one alike", () => {
    expect(refusalTitle({ path: "/a", status: 409 })).toContain("answered 409");
    expect(refusalTitle({ path: "/a", status: "409" })).toContain("answered 409");
  });
});

describe("the activity feed marks a refusal apart", () => {
  it("uses the sentence rather than the raw JSON", () => {
    // `JSON.stringify(detail)` is fine for a success — the reader is
    // skimming. It is the wrong shape for the one row they came to find.
    expect(FEED).toContain("refusalTitle(r.detail)");
  });

  it("gives it a tone, so it can be found by eye", () => {
    expect(FEED).toContain('r.action === "refused"');
    expect(FEED).toContain('chipClass("danger")');
  });
});
