// The hover dictionary — checked for the failure that makes tooltips useless.
//
// 🔴 **A tooltip that restates its label is worse than none.** It spends a
// hover, teaches nothing, and teaches the reader that hovering here is not
// worth doing. That is the state this page was in before: forty-four controls
// and eight explanations.

import { describe, expect, it } from "vitest";

import {
  HELP_AVAILABLE,
  HELP_DETAILS,
  HELP_FACTS,
  HELP_PRICING,
  HELP_FEED,
  HELP_FILL,
  HELP_STATUS,
  HELP_TIERS,
  HELP_TOOLBAR,
} from "./help";

const ALL: Record<string, Record<string, string>> = {
  HELP_FEED,
  HELP_FILL,
  HELP_TOOLBAR,
  HELP_FACTS,
  HELP_PRICING,
  HELP_STATUS,
  HELP_AVAILABLE,
  HELP_DETAILS,
  HELP_TIERS,
};

/** Every entry, as [dictionary, key, text]. */
const entries: [string, string, string][] = Object.entries(ALL).flatMap(
  ([name, dict]) =>
    Object.entries(dict).map(([k, v]) => [name, k, v] as [string, string, string]),
);

describe("every tooltip says something", () => {
  it("has entries at all", () => {
    expect(entries.length).toBeGreaterThan(25);
  });

  it.each(entries)("%s.%s is a sentence, not a fragment", (_d, _k, text) => {
    // Short enough to read on hover, long enough to explain. A three-word
    // tooltip is a label with extra steps.
    expect(text.length).toBeGreaterThan(30);
    expect(text.length).toBeLessThan(400);
  });

  it.each(entries)("%s.%s ends like a sentence", (_d, _k, text) => {
    expect(text.trim()).toMatch(/[.!?]$/);
  });

  it.each(entries)("%s.%s does not merely repeat its own key", (_d, key, text) => {
    // 🔴 The failure this file exists for. "vendorPrice: the vendor price" is
    // the shape that makes a whole tooltip layer worthless.
    const words = key.replace(/([A-Z])/g, " $1").toLowerCase().trim().split(/\s+/);
    const body = text.toLowerCase();
    const stripped = words.reduce((acc, w) => acc.split(w).join(" "), body);
    // After removing every word of the key, real explanation must remain.
    expect(stripped.trim().split(/\s+/).length).toBeGreaterThan(8);
  });
});

describe("the tooltips that carry a consequence", () => {
  it("🔴 the vendor price warns that it is NOT the customer's price", () => {
    // Reading a cost as a price inverts a margin. It is the most expensive
    // confusion available on this page, so the hover must name it.
    expect(HELP_FACTS.vendorPrice.toLowerCase()).toContain("not what a customer pays");
  });

  it("no-key says every call FAILS, not merely that a price is missing", () => {
    // `statusOf` ranks nokey above costblind for this reason. The hover must
    // not soften it into a pricing problem.
    expect(HELP_STATUS.nokey.toLowerCase()).toContain("fails");
  });

  it("costs-blind says calls still SERVE", () => {
    // An operator who reads it as an outage goes hunting for a fault that is
    // not there.
    expect(HELP_STATUS.costblind.toLowerCase()).toContain("serve");
  });

  it("the bulk fill promises it will not overwrite", () => {
    expect(HELP_FILL.fillAll.toLowerCase()).toContain("untouched");
  });

  it("an unpriced Add warns the model arrives costs blind", () => {
    expect(HELP_AVAILABLE.addUnpriced.toLowerCase()).toContain("costs blind");
  });

  it("a blank box is explained as UNKNOWN, never as zero", () => {
    expect(HELP_DETAILS.blank.toLowerCase()).toContain("zero");
  });
});

describe("every model status has a hover", () => {
  it.each(["costed", "costblind", "nokey", "undeclared"] as const)(
    "%s is explained",
    (s) => {
      expect(HELP_STATUS[s]).toBeTruthy();
    },
  );
});
