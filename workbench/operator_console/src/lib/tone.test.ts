// The one tone vocabulary — WS-31, the operator UX rebuild.
//
// ⚠️ **Rule 4's shape, enforced locally.** `control_plane/AGENTS.md` says one
// vocabulary per concept, consumed by every surface, and names the failure it
// was written after: three status vocabularies, so one board drew grey while
// the board next door was colour-coded. This app cannot import that module —
// `customer_console.md` §2.4 measures the cross-import count as ZERO — so it
// re-expresses the rule instead of borrowing the code.
//
// This file exists so the re-expression cannot rot into the thing it replaced.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { chipClass, lifecycleTone, pricingTone } from "./tone";

const SRC = join(__dirname, "..");
const CSS = readFileSync(join(SRC, "app", "globals.css"), "utf8");

describe("lifecycle", () => {
  it("separates a paying customer from a trial", () => {
    // ⚠️ A trial is not revenue. An operator scanning a customers table for
    // health must not read "trial" as "active" because both drew green.
    expect(lifecycleTone("active")).toBe("ok");
    expect(lifecycleTone("trial")).toBe("accent");
  });

  it("separates recoverable from terminal", () => {
    expect(lifecycleTone("suspended")).toBe("warn");
    expect(lifecycleTone("past_due")).toBe("warn");
    expect(lifecycleTone("cancelled")).toBe("danger");
    expect(lifecycleTone("deleted")).toBe("danger");
  });

  it("is case- and whitespace-insensitive, and never throws", () => {
    expect(lifecycleTone("  ACTIVE ")).toBe("ok");
    expect(lifecycleTone("")).toBe("neutral");
    expect(lifecycleTone("something new")).toBe("neutral");
  });
});

describe("pricing mode", () => {
  it("🔴 distinguishes deliberately free from nobody-has-set-it", () => {
    // D19.2's embeddings are absorbed on purpose. Drawing that the same as
    // `unpriced` is how a draft price ships — the exact confusion
    // `describeRate` already prevents in words, now prevented in colour too.
    expect(pricingTone("absorbed")).toBe("accent");
    expect(pricingTone("unpriced")).toBe("warn");
    expect(pricingTone("priced")).toBe("ok");
  });

  it("falls back to neutral rather than guessing", () => {
    expect(pricingTone("something-new")).toBe("neutral");
  });
});

describe("the chip class", () => {
  it("omits the tone for neutral, so `.chip` alone is the base", () => {
    expect(chipClass("neutral")).toBe("chip");
    expect(chipClass("danger")).toBe("chip danger");
    expect(chipClass("ok", "mono")).toBe("chip ok mono");
  });

  it("only emits tones globals.css can actually draw", () => {
    // 🔴 The fence that matters. A tone with no rule renders as an unstyled
    // grey pill — indistinguishable from neutral, and silently wrong, which is
    // the failure mode a vocabulary is supposed to end.
    for (const tone of ["ok", "warn", "danger", "accent"] as const) {
      expect(CSS).toContain(`.chip.${tone}`);
    }
    expect(CSS).toContain(".chip {");
  });
});
