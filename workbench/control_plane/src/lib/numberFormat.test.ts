/**
 * The one short-number formatter (owner, 2026-10-19). Spec:
 * `whatsapp_assistant_channel.md` §13.10.
 *
 * The band edges are the point. 99,999 is "1 L", never "100K": a value that
 * rounds up to the next band moves up to it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { DEFAULT_NUMBER_SYSTEM, NUMBER_SYSTEMS, compactNumber, numberSystem } from "./numberFormat.mjs";

const L = "\u00a0L";
const CR = "\u00a0Cr";

describe("numberSystem", () => {
  it("is indian by default, and for any value it does not know", () => {
    expect(DEFAULT_NUMBER_SYSTEM).toBe("indian");
    expect(NUMBER_SYSTEMS).toEqual(["indian", "international"]);
    for (const v of [undefined, null, "", "Indian", "roman", 3]) expect(numberSystem(v)).toBe("indian");
    expect(numberSystem("international")).toBe("international");
  });
});

describe("compactNumber, indian", () => {
  const f = (v: number) => compactNumber(v, "indian");

  it("writes thousands as K below a lakh", () => {
    expect([f(0), f(999), f(1000), f(1200), f(1234), f(85000), f(99_000)])
      .toEqual(["0", "999", "1K", "1.2K", "1.23K", "85K", "99K"]);
  });

  it("moves to lakh at 1e5, and 99,999 rounds up into it", () => {
    expect(f(99_999)).toBe(`1${L}`);
    expect(f(100_000)).toBe(`1${L}`);
    expect(f(1_250_000)).toBe(`12.5${L}`);
    expect(f(4_530_000)).toBe(`45.3${L}`);
  });

  it("moves to crore at 1e7, and 99.99 lakh rounds up into it", () => {
    expect(f(9_999_999)).toBe(`1${CR}`);
    expect(f(10_000_000)).toBe(`1${CR}`);
    expect(f(13_500_000)).toBe(`1.35${CR}`);
    expect(f(250_000_000)).toBe(`25${CR}`);
  });

  it("groups a large crore figure the Indian way", () => {
    expect(f(1.35e10)).toBe(`1,350${CR}`);
    expect(f(1.23456e12)).toBe(`1,23,456${CR}`);
  });

  it("keeps a sign and up to two decimals", () => {
    expect([f(-3), f(-1_250_000), f(2.345), f(0.5), f(-0.004)])
      .toEqual(["-3", `-12.5${L}`, "2.35", "0.5", "0"]);
  });
});

describe("compactNumber, international", () => {
  const f = (v: number) => compactNumber(v, "international");

  it("writes thousand, million and billion, and never lakh or crore", () => {
    expect([f(1200), f(85_000), f(150_000), f(1_500_000), f(13_500_000), f(2.5e9)])
      .toEqual(["1.2K", "85K", "150K", "1.5M", "13.5M", "2.5B"]);
  });

  it("moves up a band when rounding reaches it", () => {
    expect(f(999_999)).toBe("1M");
    expect(f(999_999_999)).toBe("1B");
    expect(f(999.996)).toBe("1K");
  });

  it("groups a large billion figure the western way", () => {
    expect(f(1.5e12)).toBe("1,500B");
  });

  it("keeps a sign", () => {
    expect(f(-2_000_000)).toBe("-2M");
  });
});

// The one table of cases that `acb_common.number_system.compact_number`, the
// Python twin for the old WhatsApp renderer, also reads. A drift between the
// two fails here or in `tests/unit/test_number_system.py`.
describe("the shared case table", () => {
  const table = JSON.parse(
    readFileSync(join(__dirname, "..", "..", "..", "..", "tests", "fixtures", "number_format_cases.json"), "utf-8"),
  ) as { cases: { value: number; indian: string; international: string }[] };

  it("holds cases, and each one agrees", () => {
    expect(table.cases.length).toBeGreaterThan(20);
    for (const c of table.cases) {
      expect([compactNumber(c.value, "indian"), compactNumber(c.value, "international")])
        .toEqual([c.indian, c.international]);
    }
  });
});
