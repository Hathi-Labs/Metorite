// Provenance — the guardrail on building the UI before the backend.
//
// ⚠️ **The subject is not "does sample data work".** It is "can sample data
// reach an operator without saying so". Every test below is a way that could
// happen, written as the failure rather than as the feature.

import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { SAMPLE_CATALOG } from "./sample";
import {
  type Origin,
  failed,
  provenanceBanner,
  resolve,
  sampleMode,
} from "./source";

describe("the sample-mode flag", () => {
  it("is OFF when nothing is set", () => {
    // 🔴 The production case. A deployment that forgot to think about this
    // must show nothing rather than fiction.
    expect(sampleMode({})).toBe(false);
  });

  it("🔴 treats '0' and 'false' as OFF, not as a non-empty string", () => {
    // A loose truthiness test turns the flag ON for both of these, which is
    // how fiction reaches a production operator while the config says it is
    // disabled.
    expect(sampleMode({ OPERATOR_CONSOLE_SAMPLE_DATA: "0" })).toBe(false);
    expect(sampleMode({ OPERATOR_CONSOLE_SAMPLE_DATA: "false" })).toBe(false);
    expect(sampleMode({ OPERATOR_CONSOLE_SAMPLE_DATA: "" })).toBe(false);
    expect(sampleMode({ OPERATOR_CONSOLE_SAMPLE_DATA: "no" })).toBe(false);
  });

  it("is on for the words a person would actually type", () => {
    for (const v of ["1", "true", "TRUE", "yes", " on "]) {
      expect(sampleMode({ OPERATOR_CONSOLE_SAMPLE_DATA: v })).toBe(true);
    }
  });
});

describe("what resolve hands a screen", () => {
  const SAMPLE = ["a", "b"];
  const EMPTY: string[] = [];
  const opts = { sample: SAMPLE, empty: EMPTY, owed: "the endpoint is owed" };

  it("passes live data through untouched", () => {
    const r = resolve({ ok: true, data: ["real"] }, opts, {});
    expect(r).toEqual({ data: ["real"], origin: "live" });
  });

  it("🔴 returns EMPTY, never the sample, when sample mode is off", () => {
    // The failure this whole module exists to prevent: a production screen
    // rendering the designed placeholder as though it were this deployment.
    const r = resolve({ ok: false }, opts, {});
    expect(r.origin).toBe("missing");
    expect(r.data).toEqual([]);
    expect(r.data).not.toBe(SAMPLE);
  });

  it("returns the sample, loudly, when sample mode is on", () => {
    const r = resolve({ ok: false }, opts, { OPERATOR_CONSOLE_SAMPLE_DATA: "1" });
    expect(r.origin).toBe("sample");
    expect(r.data).toEqual(SAMPLE);
    expect(r.note).toBe("the endpoint is owed");
  });

  it("🔴 reports a REFUSAL as an error, even in sample mode", () => {
    // ⚠️ An endpoint that exists and answered 500 is not an unbuilt feature.
    // Painting it as one hides an outage behind a roadmap note, and nobody
    // goes to look.
    const r = resolve(
      { ok: false, note: "The Console answered 500." },
      opts,
      { OPERATOR_CONSOLE_SAMPLE_DATA: "1" },
    );
    expect(r.origin).toBe("error");
    expect(r.data).toEqual([]);
  });
});

describe("the banner", () => {
  it("🔴 is silent for live data and for NOTHING else", () => {
    expect(provenanceBanner("live")).toBeNull();
    for (const o of ["sample", "missing", "error"] as Origin[]) {
      expect(provenanceBanner(o, "why")).not.toBeNull();
    }
  });

  it("says the numbers are not real, in those words", () => {
    const b = provenanceBanner("sample", "the endpoint is owed");
    expect(b?.text).toContain("SAMPLE DATA");
    expect(b?.text).toContain("none of the numbers below are real");
    expect(b?.text).toContain("the endpoint is owed");
  });

  it("keeps a refusal at danger and a gap at info", () => {
    expect(provenanceBanner("error", "500")?.tone).toBe("danger");
    expect(provenanceBanner("missing", "owed")?.tone).toBe("info");
  });

  it("carries the refusal text rather than a paraphrase of it", () => {
    const r = failed([], "The Console answered 403. elevation required");
    expect(provenanceBanner(r.origin, r.note)?.text).toContain("403");
  });
});

// ── The structural fence ────────────────────────────────────────────────────

const APP = join(__dirname, "..", "app");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (p.endsWith(".ts") || p.endsWith(".tsx")) out.push(p);
  }
  return out;
}

/** ⚠️ **Strip comments before scanning.** A page that EXPLAINS why it does not
 *  import the sample would otherwise fail its own fence, and the fix somebody
 *  reaches for is deleting the explanation. This repo has hit a guard matching
 *  its own prose seven times. */
const code = (f: string) =>
  readFileSync(f, "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ");

describe("nothing under src/app can reach the sample directly", () => {
  it("🔴 no page imports @/lib/sample", () => {
    // The seam in one assertion. Sample data reaches a screen through
    // `read.ts`, which stamps the origin that makes the banner appear. An
    // import that skipped it would render fiction with nothing above it.
    const offenders = walk(APP)
      .filter((f) => !f.endsWith(".test.ts") && !f.endsWith(".test.tsx"))
      .filter((f) => /from\s+["']@\/lib\/sample["']/.test(code(f)));
    expect(offenders).toEqual([]);
  });

  it("the sample catalog is big enough to design against", () => {
    // A placeholder with two rows hides every layout problem that matters.
    expect(SAMPLE_CATALOG.models.length).toBeGreaterThan(12);
    expect(SAMPLE_CATALOG.tiers.length).toBeGreaterThan(2);
  });

  it("🔴 the sample holds UNHEALTHY shapes, not a happy path", () => {
    // ⚠️ A designer who only ever sees green ships the red states untested.
    expect(SAMPLE_CATALOG.accounts.some((a) => a.revokedAt)).toBe(true);
    expect(SAMPLE_CATALOG.models.some((m) => !m.declared)).toBe(true);
    // D67: "unpriced" is a TIER state now - the sample must hold a bound
    // job with no decided tier rate, so the no-price chip renders.
    expect(SAMPLE_CATALOG.tiers.some((t) => t.registered && t.jobs.length > 0
      && t.jobs.some((j) => !SAMPLE_CATALOG.tierRates.some(
        (r) => r.tier === t.slug && r.task === j.task && r.mode !== "unpriced",
      )))).toBe(true);
    expect(SAMPLE_CATALOG.tiers.some((t) => t.jobs.some((j) => j.chain.length === 1)))
      .toBe(true);
  });

  it("🔴 the sample holds NO probed health, because nothing probes", () => {
    // This asserted the opposite until 2026-08-30 — a `failing` account with
    // the note "401 from the vendor". Nothing on the backend measures vendor
    // health, so a sample that models a probe teaches a feature that does not
    // exist, and the owner reads it as the page's meaning. Unhealthy is good
    // sample data; IMPOSSIBLE is not.
    for (const a of SAMPLE_CATALOG.accounts) {
      expect(a.health).toBe("unknown");
      expect(a.lastCheckedAt).toBeNull();
      expect(a.healthNote).toBeNull();
    }
  });

  it("🔴 the sample never holds two live platform keys for one vendor", () => {
    // `provider_credential_live_uniq` refuses the second insert — proved
    // against the real schema on 2026-08-30. The sample carried exactly that
    // state ("Main billing account" + "Overflow account") and the card grew
    // UI for it.
    const seen = new Set<string>();
    for (const a of SAMPLE_CATALOG.accounts) {
      if (a.revokedAt || a.orgSlug) continue;
      expect(seen.has(a.provider), `two live platform keys for ${a.provider}`)
        .toBe(false);
      seen.add(a.provider);
    }
  });
});
