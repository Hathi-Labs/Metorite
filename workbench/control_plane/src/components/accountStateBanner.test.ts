import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

import { bannerFor, daysUntil } from "./AccountStateBanner";

/**
 * Fences for CP-2j's copy. Pure functions, so the wording is testable without
 * rendering — the same reason `errorCopy` is a pure module.
 */

const NOW = new Date("2026-09-15T12:00:00Z");
const iso = (days: number) =>
  new Date(NOW.getTime() + days * 86_400_000).toISOString();

describe("daysUntil", () => {
  it("counts whole days, rounding UP", () => {
    // With 18 hours left a member is in their LAST day. Rounding down would
    // say "0 days left", which reads as already over.
    expect(daysUntil(iso(0.75), NOW)).toBe(1);
    expect(daysUntil(iso(12), NOW)).toBe(12);
  });

  it("goes negative once the deadline has passed", () => {
    expect(daysUntil(iso(-2), NOW)).toBeLessThanOrEqual(-1);
  });

  it("returns null for absent or unparseable input", () => {
    for (const bad of [null, undefined, "", "not a date"]) {
      expect(daysUntil(bad, NOW)).toBeNull();
    }
  });
});

describe("bannerFor", () => {
  it("says the state AND the days left on a healthy trial", () => {
    // The owner's directive, 2026-09-15: name the state and the days left.
    const banner = bannerFor("trial", iso(12), NOW);
    expect(banner?.text).toContain("12 days left");
    expect(banner?.text).toMatch(/activates once we confirm payment/i);
    expect(banner?.tone).toBe("info");
  });

  it("uses the singular for one day", () => {
    expect(bannerFor("trial", iso(0.5), NOW)?.text).toContain("1 day left");
    expect(bannerFor("trial", iso(0.5), NOW)?.text).not.toContain("1 days");
  });

  it("escalates the tone near the end of the trial", () => {
    expect(bannerFor("trial", iso(10), NOW)?.tone).toBe("info");
    expect(bannerFor("trial", iso(2), NOW)?.tone).toBe("warn");
  });

  it("names the state without a number when the deadline never reached us", () => {
    // On trial, but the registry did not tell us when it ends. Say the state,
    // never guess the number.
    const banner = bannerFor("trial", null, NOW);
    expect(banner).not.toBeNull();
    expect(banner?.text).toMatch(/free trial/i);
    expect(banner?.text).not.toMatch(/\d/);
  });

  it("says an expired trial has ended rather than counting backwards", () => {
    const banner = bannerFor("trial", iso(-3), NOW);
    expect(banner?.tone).toBe("warn");
    expect(banner?.text).toMatch(/has ended/i);
    expect(banner?.text).not.toContain("-3");
  });

  it("tells a past_due customer that everything STILL WORKS", () => {
    // `past_due` is grace by design. Copy that reads like a shutdown makes
    // people stop using the product before anything is locked, which is the
    // opposite of what the grace period is for.
    const banner = bannerFor("past_due", null, NOW);
    expect(banner?.text).toMatch(/still works/i);
  });

  it("tells a suspended customer their data is safe and paying restores it", () => {
    const banner = bannerFor("suspended", null, NOW);
    expect(banner?.text).toMatch(/data is safe/i);
    expect(banner?.text).toMatch(/restores access/i);
  });

  it("tells a cancelled customer they can still export", () => {
    // Sign-in stays open through `cancelled` precisely so the export window is
    // possible. The banner must say so, or the window is invisible.
    expect(bannerFor("cancelled", null, NOW)?.text).toMatch(/export/i);
  });

  it("says NOTHING for an active org", () => {
    // The overwhelmingly common case. A banner on every screen for a customer
    // with nothing to do is how people learn to ignore banners.
    expect(bannerFor("active", null, NOW)).toBeNull();
  });

  it("says NOTHING when the registry word is absent or unknown", () => {
    // A deployment whose resolve flag has never been on has no registry word.
    // Silence is the honest answer; an invented state is not.
    for (const unknown of [null, undefined, "", "banana", "deleted"]) {
      expect(bannerFor(unknown, iso(5), NOW), String(unknown)).toBeNull();
    }
  });
});

describe("the banner is presentation, never a gate", () => {
  const source = readFileSync(
    new URL("./AccountStateBanner.tsx", import.meta.url),
    "utf-8",
  );

  it("reads is_admin but never decides access from the registry word", () => {
    // ⚠️ The rule this pins. `registry_status` is a CACHED word and
    // `trial_ends_at` a CACHED date. A surface that hid a pane on either would
    // lock out a paying customer the gateway would have admitted — on a stale
    // row, or on a clock skew.
    expect(source).toContain("access.is_admin");
    // The gating seams by name — importing any of them here is the drift.
    expect(source).not.toContain("canSeePath");
    expect(source).not.toContain("canUseFeature");
    // And it must not READ the access sets. Asserted as property accesses
    // rather than as bare words, because the file's own comment explains what
    // it deliberately does not do, and a substring test cannot tell the
    // explanation from the act.
    expect(source).not.toMatch(/access\.features/);
    expect(source).not.toMatch(/access\.capabilities/);
    expect(source).not.toMatch(/access\.denied/);
  });

  it("shows commercial state to ADMINS only", () => {
    // Trial deadlines and payment state are the owner's business. Showing every
    // member "we haven't received your payment" hands them a worry they cannot
    // act on, about a company matter that is not theirs to see.
    expect(source).toMatch(/if \(!access\.is_admin\) return null;/);
  });

  it("renders nothing before the first resolution lands", () => {
    // Flashing "your trial has ended" during load, at a customer who is fine,
    // is worse than showing nothing.
    expect(source).toMatch(/if \(loading[^)]*\) return null;/);
  });
});
