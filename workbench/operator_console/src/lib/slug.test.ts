import { describe, it, expect } from "vitest";
import { RESERVED_LABELS, SLUG_RE, slugProblem, suggestSlug } from "./slug";

// The cases that moved here from `format.test.ts` when `suggestSlug` moved out
// of `format.ts`, plus the ones the old implementation would have FAILED.
//
// ⚠️ Cross-language parity (this file vs `subdomain.ts` vs the two Python
// twins) is NOT asserted here — `tests/unit/test_subdomain_host_vocabulary.py`
// owns that, because it can read all four. This file asserts behaviour.

describe("suggestSlug", () => {
  it("lowercases, hyphenates and strips accents/symbols", () => {
    expect(suggestSlug("Fracktal Works Pvt. Ltd.")).toBe("fracktal-works-pvt-ltd");
    expect(suggestSlug("  Café  Nine!  ")).toBe("cafe-nine");
    expect(suggestSlug("---")).toBe("");
  });

  it("never suggests something the Console would refuse", () => {
    // The contract the canonical module owes, and the one the drifted copy
    // broke: the result is EITHER "" or a string SLUG_RE accepts.
    const names = [
      "Fracktal Works Pvt. Ltd.",
      "  Café  Nine!  ",
      "---",
      "!!!",
      "A",
      "Ünïcødé Ltd",
      "Some Extremely Long Indian Manufacturing Company Private Limited X",
      "trailing hyphen -",
    ];
    for (const name of names) {
      const slug = suggestSlug(name);
      expect(slug === "" || SLUG_RE.test(slug), `${name} → ${slug}`).toBe(true);
    }
  });

  it("does not leave a trailing hyphen when the length cut lands on one", () => {
    // The exact defect in the 40-character copy that used to live in
    // `format.ts`: it sliced and never re-trimmed, so this produced a value
    // ending in "-", which is not a DNS label.
    const name = "a".repeat(62) + " " + "b".repeat(20);
    const slug = suggestSlug(name);
    expect(slug.endsWith("-")).toBe(false);
    expect(SLUG_RE.test(slug)).toBe(true);
  });

  it("keeps 63 characters, not 40", () => {
    // The drifted copy cut at 40. A company whose name is longer than that
    // silently lost the tail, and two different long names could collide on one
    // slug — on the cross-plane JOIN KEY.
    expect(suggestSlug("a".repeat(80))).toHaveLength(63);
  });
});

describe("slugProblem", () => {
  it("says nothing about an empty field", () => {
    // Empty is "not yet", not "wrong". Shouting at a field nobody has typed in
    // is how a form teaches people to ignore its messages.
    expect(slugProblem("")).toBeNull();
    expect(slugProblem("   ")).toBeNull();
  });

  it("accepts a well-formed slug", () => {
    expect(slugProblem("fracktal-works")).toBeNull();
    expect(slugProblem("a")).toBeNull();
    expect(slugProblem("a1")).toBeNull();
  });

  it("names the SHAPE problem for a malformed one", () => {
    for (const bad of ["Fracktal", "with space", "under_score", "-lead", "trail-"]) {
      expect(slugProblem(bad), bad).toMatch(/lowercase letters/i);
    }
  });

  it("names a reserved label as reserved, NOT as malformed", () => {
    // Two causes, two sentences — the same split the gateway makes between
    // InvalidSlug and ReservedSlug. Calling `api` malformed would send the
    // operator to fix a thing that is not wrong.
    for (const reserved of ["api", "app", "www", "operator"]) {
      expect(slugProblem(reserved), reserved).toMatch(/reserved/i);
    }
  });

  it("refuses the reserved labels that made this a LIVE defect", () => {
    // `api` names the gateway's own hostname. Before 2026-09-15 an operator
    // could create it: the form suggested freely and the Console checked
    // nothing.
    expect(RESERVED_LABELS).toContain("api");
    expect(slugProblem("api")).not.toBeNull();
  });

  it("is case-insensitive about the reserved set", () => {
    // "API" fails the SHAPE check first, so it must not fall through as
    // acceptable just because the reserved set is lowercase.
    expect(slugProblem("API")).not.toBeNull();
  });
});
