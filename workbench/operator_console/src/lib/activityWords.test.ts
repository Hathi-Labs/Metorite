// WS-50 slice 5 — the Activity page in words, and no internal codes on screen.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { actionWords, describeDetail } from "./activityWords";
import { goLiveSteps } from "./golive";
import { EMPTY_CATALOG } from "./contract";

describe("actionWords", () => {
  it("says what happened", () => {
    expect(actionWords("credits.grant")).toBe("Added credits");
    expect(actionWords("org.lifecycle")).toBe("Changed account access");
  });

  it("keeps a new code readable rather than blank", () => {
    expect(actionWords("brand.new_thing")).toBe("brand.new_thing");
  });
});

describe("describeDetail", () => {
  it("prints name: value pairs, never a JSON blob", () => {
    expect(describeDetail({ delta: "500", reason: "manual", ref: null })).toBe(
      "credits: 500 · reason: manual",
    );
  });

  it("cuts a long value", () => {
    expect(describeDetail({ note: "x".repeat(200) }).length).toBeLessThan(100);
  });
});

// ── No internal code on a page a person reads ─────────────────────────────

const CODES = /\bH-\d{1,3}\b|\bD\d{2}(?:\.\d+)?\b|migration \d{3}|\(CP-\d+\)/;

function strip(src: string): string {
  return src
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|\s)\/\/[^\n]*/g, "$1");
}

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === "api" ? [] : files(p);
    return /\.tsx?$/.test(name) && !/\.test\./.test(name) ? [p] : [];
  });
}

/** Lines left after comments are gone. Wrapped JSX text carries no quote,
 *  so every remaining line is checked, not only the ones with a string. */
function visibleHits(dir: string): string[] {
  return files(dir).flatMap((f) =>
    strip(readFileSync(f, "utf-8"))
      .split("\n")
      .filter((l) => CODES.test(l))
      .map((l) => `${f}: ${l.trim()}`),
  );
}

describe("no ticket, decision or migration number reaches the screen", () => {
  it("in any page or component (comments are allowed)", () => {
    expect(visibleHits(join(__dirname, "..", "app"))).toEqual([]);
  });

  it("in any lib string a page renders (comments are allowed)", () => {
    expect(visibleHits(__dirname)).toEqual([]);
  });

  it("in the setup checklist's sentences", () => {
    for (const s of goLiveSteps(EMPTY_CATALOG, [])) {
      expect(`${s.title} ${s.detail}`, s.key).not.toMatch(CODES);
    }
  });
});
