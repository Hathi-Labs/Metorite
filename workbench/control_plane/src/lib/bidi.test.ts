// The one rule of the bidi controls (`lib/bidi.ts`, MCP-P1b-1 re-review P3).
//
// The characters are built with `String.fromCodePoint`, so this file holds no
// control as a literal (the Trojan Source pattern).
//
// Mutations, each one red here:
// - give HAS_BIDI_CONTROL the `g` flag: the "asks twice" case fails;
// - drop one range from the class: the "each control" case fails.
import { describe, expect, it } from "vitest";

import { BIDI_CONTROLS, HAS_BIDI_CONTROL, withoutBidi } from "@/lib/bidi";

const CODES = [0x061c, 0x200e, 0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069];
const ch = (code: number) => String.fromCodePoint(code);

describe("lib/bidi", () => {
  it("drops each control of the set, and keeps the joiners", () => {
    for (const code of CODES) expect(withoutBidi(`a${ch(code)}b`)).toBe("ab");
    const joined = `a${ch(0x200d)}b${ch(0x200c)}c`;
    expect(withoutBidi(joined)).toBe(joined);
  });

  it("asks twice with the same answer: the asking form has no `g` flag", () => {
    expect(BIDI_CONTROLS.flags).toContain("g");
    expect(HAS_BIDI_CONTROL.flags).not.toContain("g");
    const text = `x${ch(0x202e)}y`;
    expect(HAS_BIDI_CONTROL.test(text)).toBe(true);
    expect(HAS_BIDI_CONTROL.test(text)).toBe(true);
    expect(HAS_BIDI_CONTROL.test("plain")).toBe(false);
    expect(HAS_BIDI_CONTROL.source).toBe(BIDI_CONTROLS.source);
  });
});
