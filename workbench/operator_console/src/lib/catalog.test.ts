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

import { describeRate, singular, type RateRow } from "./catalog";

const SRC = join(__dirname, "..");
const ADMIN = readFileSync(join(SRC, "app", "models", "CatalogAdmin.tsx"), "utf8");
const PAGE = readFileSync(join(SRC, "app", "models", "page.tsx"), "utf8");
const HEADER = readFileSync(join(SRC, "app", "Header.tsx"), "utf8");

const CARD = (over: Partial<RateRow> = {}): RateRow => ({
  model: "m",
  task: "chat",
  unit: "tokens",
  pricing_mode: "priced",
  input_per_1k: "2",
  output_per_1k: "6",
  cached_input_per_1k: "0.5",
  credits_per_unit: "0",
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
        input_per_1k: "0", output_per_1k: "0", credits_per_unit: "0.4",
      })),
    ).toBe("0.4 per minute");
  });

  it("distinguishes absorbed from not priced", () => {
    // D19.2's embeddings are deliberately free. Saying "free" where the card
    // means "nobody has set this yet" is how a draft price ships.
    expect(describeRate(CARD({ pricing_mode: "absorbed" })))
      .toContain("absorbed");
    expect(describeRate(CARD({ pricing_mode: "unpriced" })))
      .toBe("not priced");
  });

  it("never shows a token rate for a non-token card", () => {
    const line = describeRate(CARD({
      task: "image", unit: "images", credits_per_unit: "3",
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
  it("shows the UNSERVED gap as an alarm, not a list", () => {
    // ⚠️ Bound but NOT capable is a 500 waiting for the first request. It is
    // the only thing on this page that is actually broken, so it is the only
    // thing rendered as a banner.
    const unserved = ADMIN.indexOf("data.unserved.length");
    const unbound = ADMIN.indexOf("data.unbound.length");
    expect(unserved).toBeGreaterThan(-1);
    expect(unbound).toBeGreaterThan(-1);
    // Both ABOVE the tables — an operator will not diff two tables by eye.
    expect(unserved).toBeLessThan(ADMIN.indexOf("Tier bindings"));
    expect(unbound).toBeLessThan(ADMIN.indexOf("Tier bindings"));
    // And the broken one first.
    expect(unserved).toBeLessThan(unbound);
  });

  it("relays a refusal VERBATIM", () => {
    // The Console is the authority on a refusal — it knows the unit rule, the
    // capability rule and the pricing-mode rule. Paraphrasing here would be a
    // second vocabulary for the same 400.
    expect(ADMIN).toContain("The Console refused:");
    expect(ADMIN).toContain("result.text");
  });

  it("drives the four catalog routes and no others", () => {
    expect(ADMIN).toContain("/api/operator/catalog/bindings");
    expect(ADMIN).toContain("/api/operator/catalog/capabilities");
    // ⚠️ The page READS through the server component, not a client fetch, so
    // `/models` is absent here on purpose.
    expect(PAGE).toContain("readModelCatalog");
  });

  it("says out loud that the card ships unpriced", () => {
    // 🔴 Setting a real number is the owner's commercial act (H-42). An
    // operator reading this page must not think the zeros are an oversight
    // they should quietly fix.
    expect(ADMIN).toContain("unpriced");
  });

  it("is reachable from the top bar", () => {
    expect(HEADER).toContain('{ href: "/models", label: "Models" }');
  });

  it("offers no way to EDIT a binding or a rate", () => {
    // §6A.5: both are INSERT-only. A past invoice must stay readable against
    // what it was actually charged on, so there is no PATCH and no DELETE.
    expect(ADMIN).not.toContain('method: "PATCH"');
    expect(ADMIN).not.toContain('method: "DELETE"');
    expect(ADMIN).not.toContain('method: "PUT"');
  });
});
