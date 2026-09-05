import { describe, expect, it } from "vitest";

import { timesThousand } from "./scale";

// Spec: `project-docs/specs/credit_pricing.md` §4.2 (slice 3). Migration 025.
//
// 🔴 **This exists because `Number(v) * 1000` is a money bug.** Measured over
// the 20000 rates between 0.0001 and 2.0000: 4773 of them come back with a
// float artefact. `0.0041` becomes `4.1000000000000005`, and that string then
// reaches a price column or a comparison and stops matching itself.

describe("restating a money string at the per-million scale", () => {
  it("is EXACT on the values a float gets wrong", () => {
    // Each of these is a documented artefact of `Number(v) * 1000`.
    expect(timesThousand("0.0041")).toBe("4.1");
    expect(timesThousand("0.0049")).toBe("4.9");
    expect(timesThousand("0.0051")).toBe("5.1");
    expect(timesThousand("0.0059")).toBe("5.9");
    expect(timesThousand("0.0061")).toBe("6.1");
  });

  it("never emits a float artefact across the whole realistic range", () => {
    // 🔴 The property, not five examples. Any output carrying more than three
    // decimals came from binary floating point, because shifting a four-place
    // decimal by three places cannot produce a fourth.
    for (let i = 1; i <= 20000; i++) {
      const v = (i / 10000).toFixed(4);
      const out = timesThousand(v);
      expect(out, `${v} produced ${out}`).toMatch(/^\d+(\.\d)?$/);
    }
  });

  it("handles a whole number and a value with no fraction left", () => {
    expect(timesThousand("2")).toBe("2000");
    expect(timesThousand("0.0080")).toBe("8");
    expect(timesThousand("0.175")).toBe("175");
  });

  it("keeps precision a float would round away", () => {
    expect(timesThousand("0.0000005")).toBe("0.0005");
    expect(timesThousand("0.000000000001")).toBe("0.000000001");
  });

  it("treats zero as a number, because zero is a PRICE", () => {
    // An absorbed tier is free on purpose (D19.2). Zero must survive as zero
    // and must never read as "absent".
    expect(timesThousand("0")).toBe("0");
    expect(timesThousand("0.0000")).toBe("0");
  });

  it("returns an unparseable value unchanged rather than a silent zero", () => {
    // A caller holding something odd is better served by its own value
    // reaching the display than by a fabricated number.
    expect(timesThousand("")).toBe("");
    expect(timesThousand("n/a")).toBe("n/a");
  });

  it("carries a sign through", () => {
    expect(timesThousand("-0.0041")).toBe("-4.1");
  });
});
