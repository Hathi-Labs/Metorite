// WS-50 slice 2 — the glossary is the one meaning of each money word (D94).

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { GLOSSARY } from "./glossary";

const entries = Object.entries(GLOSSARY);

describe("GLOSSARY", () => {
  it("gives every entry a title, a meaning and a formula", () => {
    for (const [key, e] of entries) {
      expect(e.title.length, key).toBeGreaterThan(0);
      expect(e.means.split(/\s+/).length, key).toBeGreaterThanOrEqual(8);
      expect(e.formula.length, key).toBeGreaterThan(0);
    }
  });

  it("names no internal code: no H- or D-number, migration, env var or product jargon", () => {
    const banned = [/\bH-\d+/, /\bD\d{1,3}\b/, /migration \d+/i, /[A-Z]{3,}_[A-Z_]{3,}/, /\bRouter\b/, /\bBYOK\b/, /litellm/i];
    for (const [key, e] of entries) {
      const text = `${e.title} ${e.means} ${e.formula}`;
      for (const re of banned) expect(text, `${key} matches ${re}`).not.toMatch(re);
    }
  });

  it("says AI cost is OUR cost, not the customer's price — the owner's question", () => {
    expect(GLOSSARY.aiCost.means).toMatch(/billed US/);
    expect(GLOSSARY.aiCost.means).toMatch(/not the customer's price/);
  });

  it("says a free credit is not revenue (D94 rule 1)", () => {
    expect(GLOSSARY.weCharged.means).toMatch(/Free credits are not counted/);
  });
});

// ── The retired words ─────────────────────────────────────────────────────

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return files(p);
    return /\.tsx$/.test(name) ? [p] : [];
  });
}

describe("the customer money panels", () => {
  const dir = join(__dirname, "..", "app", "customers", "[slug]");
  const usage = readFileSync(join(dir, "CustomerUsage.tsx"), "utf-8");
  const breakdown = readFileSync(join(dir, "CustomerBreakdown.tsx"), "utf-8");

  it("no longer print the ambiguous labels", () => {
    for (const src of [usage, breakdown]) {
      expect(src).not.toMatch(/>\s*Our cost\s*</);
      expect(src).not.toMatch(/>\s*Cost to us\s*</);
      expect(src).not.toMatch(/marginLabel\(/);
    }
  });

  it("give every money heading an Explain", () => {
    for (const term of ["weCharged", "aiCost", "profit", "margin", "daysLeft"]) {
      expect(usage, term).toContain(`term="${term}"`);
    }
    // The table leaves seats out, so it must NOT borrow "We charged", whose
    // meaning includes seats (review, S1+S2).
    for (const term of ["aiRevenue", "aiCost", "aiMargin"]) {
      expect(breakdown, term).toContain(`term="${term}"`);
    }
    expect(breakdown).not.toContain('term="weCharged"');
  });

  it("only tsx files under app/ import the glossary through Explain", () => {
    const app = join(__dirname, "..", "app");
    const direct = files(app).filter(
      (f) => !f.endsWith("Explain.tsx") && readFileSync(f, "utf-8").includes("@/lib/glossary"),
    );
    expect(direct).toEqual([]);
  });
});
