// WS-31 CP-10 slice 3b — the operator's model catalog surface.
//
// Spec: `specs/customer_console.md` §6A · §6A.9 · D60 · D61.
//
// ⚠️ **This surface RELOCATES a capability across the tenancy boundary.** A
// complete three-tab model console already exists in the CUSTOMER product at
// `settings/models/page.tsx`, hidden only by D49's `preview`. Model operations
// are an operator concern — the keys are ours, the rate card is ours, and a
// customer must never see a model at all (D32.7).

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { describeRate, singular, type ModelRate } from "./catalog";

const SRC = join(__dirname, "..");
const BROWSER = readFileSync(join(SRC, "app", "models", "ModelBrowser.tsx"), "utf8");
const RATECARD = readFileSync(join(SRC, "app", "models", "RateCard.tsx"), "utf8");
const DECLARE = readFileSync(join(SRC, "app", "models", "DeclareModel.tsx"), "utf8");
const TIERS = readFileSync(join(SRC, "app", "tiers", "TierBoard.tsx"), "utf8");
const PAGE = readFileSync(join(SRC, "app", "models", "page.tsx"), "utf8");
const HEADER = readFileSync(join(SRC, "app", "Header.tsx"), "utf8");

/** ⚠️ Strip comments before scanning. A file that EXPLAINS why it does not do
 *  something must not fail the fence that checks it does not. Seven times in
 *  this repo, and counting. */
const code = (t: string) =>
  t.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/^\s*\/\/.*$/gm, " ");

const CARD = (over: Partial<ModelRate> = {}): ModelRate => ({
  model: "m",
  task: "chat",
  unit: "tokens",
  mode: "priced",
  inputPer1k: "2",
  outputPer1k: "6",
  cachedInputPer1k: "0.5",
  creditsPerUnit: "0",
  ...over,
});

describe("the rate line", () => {
  it("reads the per-1k columns for a token card", () => {
    expect(describeRate(CARD())).toBe("2 in / 6 out per 1k");
  });

  it("reads credits_per_unit for every other unit", () => {
    // ⚠️ The whole point. An operator shown "0.4 per 1k" for a card that
    // charges 0.4 per MINUTE would sign off on a price forty times wrong.
    expect(
      describeRate(CARD({
        task: "transcribe", unit: "minutes",
        inputPer1k: "0", outputPer1k: "0", creditsPerUnit: "0.4",
      })),
    ).toBe("0.4 per minute");
  });

  it("distinguishes absorbed from not priced", () => {
    // D19.2's embeddings are deliberately free. Saying "free" where the card
    // means "nobody has set this yet" is how a draft price ships.
    expect(describeRate(CARD({ mode: "absorbed" })))
      .toContain("absorbed");
    expect(describeRate(CARD({ mode: "unpriced" })))
      .toBe("not priced");
  });

  it("never shows a token rate for a non-token card", () => {
    const line = describeRate(CARD({
      task: "image", unit: "images", creditsPerUnit: "3",
    }));
    expect(line).not.toContain("per 1k");
    expect(line).toBe("3 per image");
  });
});

describe("unit names read as English", () => {
  it("singularises the units we actually use", () => {
    expect(singular("minutes")).toBe("minute");
    expect(singular("images")).toBe("image");
    expect(singular("characters")).toBe("character");
  });

  it("leaves a unit that is already singular alone", () => {
    expect(singular("token")).toBe("token");
  });
});

describe("the surface", () => {
  // 🔴 **These fences moved because the page split in two.** `/models` used to
  // hold the catalog AND the tier bindings, which is why it was confusing: a
  // reader had to know which of two questions they were answering. Finding a
  // model is one job, deciding what a tier runs on is another, and the fences
  // follow the jobs.

  it("the catalog can be SEARCHED and filtered, not just read", () => {
    // ⚠️ The reason this page was rebuilt. Three stacked tables were fine with
    // one provider. OpenRouter alone exposes two hundred models, and an
    // operator asked "which of these reads an image" had nowhere to ask it.
    expect(code(BROWSER)).toContain("filterModels");
    expect(code(BROWSER)).toContain("kindFacets");
    expect(code(BROWSER)).toContain('type="search"');
  });

  it("🔴 the catalog runs NO fetch of its own", () => {
    // The list is read by the server component with the caller's own token. A
    // client fetch would put a cross-tenant read on a path the browser can
    // replay, and would reach the Console as `breakglass`.
    expect(code(BROWSER)).not.toContain("fetch(");
    expect(PAGE).not.toContain("use client");
    expect(PAGE).toContain("readAiCatalog");
  });

  it("says out loud that a card can ship with no price", () => {
    // 🔴 Setting a real number is the owner's commercial act (H-42). An
    // operator must not read the zeros as an oversight to quietly fix.
    expect(code(RATECARD)).toContain("no price");
    expect(code(RATECARD)).toContain("charge nothing");
  });

  it("keeps the vendor's price and OUR price apart, in words", () => {
    // ⚠️ Two numbers, two tables, and reading one as the other inverts a
    // margin. The catalog says what we PAY; the rate card says what we CHARGE.
    expect(code(BROWSER)).toContain("We pay");
    expect(code(RATECARD)).toContain("not what the vendor charges us");
  });

  it("relays a refusal VERBATIM, on both surfaces that write", () => {
    // The Console is the authority on a refusal — it knows the unit rule, the
    // capability rule and the pricing-mode rule. Paraphrasing here would be a
    // second vocabulary for the same 400.
    expect(code(TIERS)).toContain("The Console refused:");
    expect(code(DECLARE)).toContain("The Console refused:");
  });

  it("drives the two catalog write routes, each from its own surface", () => {
    expect(code(TIERS)).toContain("/api/operator/catalog/bindings");
    expect(code(DECLARE)).toContain("/api/operator/catalog/capabilities");
  });

  it("is reachable from the sidebar, and so are its neighbours", () => {
    // ⚠️ Asserted as two facts rather than one object literal. The nav entry
    // grew an `icon` field when the top bar became a sidebar (2026-08-29), and
    // a test pinned to the literal broke on a change that did not touch
    // reachability at all — which is the thing this test is actually for.
    expect(HEADER).toContain('href: "/models"');
    expect(HEADER).toContain('label: "Models"');
    expect(HEADER).toContain('href: "/tiers"');
  });

  it("offers no way to EDIT a binding or a rate", () => {
    // §6A.5: both are INSERT-only. A past invoice must stay readable against
    // what it was actually charged on, so there is no PATCH and no DELETE.
    for (const f of [TIERS, DECLARE, RATECARD, BROWSER]) {
      expect(code(f)).not.toContain('method: "PATCH"');
      expect(code(f)).not.toContain('method: "DELETE"');
      expect(code(f)).not.toContain('method: "PUT"');
    }
  });

  it("🔴 posts the WHOLE chain, never just the primary", () => {
    // The hazard that replaced the disabled button. The Console writes every
    // step at one `effective_from`, so a request carrying only `model` REPLACES
    // the chain with a one-step chain — silently deleting the backups the
    // operator just added, and leaving a page that still shows them.
    expect(code(TIERS)).toContain("tier, task, models");
    expect(code(TIERS)).not.toMatch(/body:\s*JSON\.stringify\(\{\s*tier,\s*task,\s*model\s*\}/);
  });

  it("judges the chain being EDITED, not the one already saved", () => {
    // ⚠️ An operator adding a second Anthropic model must be told it is not a
    // real backup BEFORE they save it. Judging the saved chain would show the
    // warning one click too late, against a chain they have already changed.
    const call = code(TIERS).indexOf("chainProblems(shown, ctx)");
    expect(call).toBeGreaterThan(-1);
  });

});
