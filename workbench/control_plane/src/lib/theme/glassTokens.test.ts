/**
 * The glass `--cc-*` values (GL-10a, D143).
 *
 * `globals.css` is the SOURCE and `glassTokens.ts` is a MIRROR for the
 * sandboxed frames, which have no stylesheet. This parses the two glass
 * blocks and fails when a mirrored value drifts, as `themes.test.ts` does for
 * classic. It also holds the two promises of the seam: the `--cc-*` NAMES do
 * not change, and a classic frame is byte for byte what it was.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { CC_TOKEN_NAMES, appTokenCss, appTokenMap, appTokens } from "./app-tokens";
import {
  GLASS_CC_COLOURS,
  GLASS_CC_SHARED,
  GLASS_CC_SHARED_SOURCE,
  GLASS_CC_SOURCE,
  documentLook,
} from "./glassTokens";
import { buildSrcDoc } from "./sandbox-frame";
import { THEME } from "./themes";
import type { ThemeMode } from "./types";

const GLOBALS = readFileSync(fileURLToPath(new URL("../../app/globals.css", import.meta.url)), "utf8");

function block(head: string): Record<string, string> {
  const start = GLOBALS.indexOf(head);
  if (start === -1) throw new Error(`No \`${head}\` block in globals.css`);
  const open = GLOBALS.indexOf("{", start);
  const close = GLOBALS.indexOf("}", open);
  const out: Record<string, string> = {};
  for (const m of GLOBALS.slice(open + 1, close).matchAll(/^\s*(--[\w-]+)\s*:\s*([^;]+);/gm)) {
    out[m[1]] = m[2].trim();
  }
  return out;
}

const DARK = block('html[data-look="glass"] {');
const LIGHT = block('html[data-look="glass"].light {');

/** The value of a glass property in a mode: light restates, else dark, and `var(--x)` resolves once. */
function cssValue(prop: string, mode: ThemeMode): string {
  const own = mode === "light" ? (LIGHT[prop] ?? DARK[prop]) : DARK[prop];
  const ref = /^var\((--[\w-]+)\)$/.exec(own ?? "");
  return ref ? cssValue(ref[1], mode) : own;
}

/** Drop the `next/font` handle, as `stripFontHandles` does. */
const noHandle = (stack: string) =>
  stack
    .split(",")
    .map((p) => p.trim())
    .filter((p) => !/^var\(\s*--font-/i.test(p))
    .join(", ");

describe("glass --cc-* values mirror globals.css", () => {
  for (const mode of ["dark", "light"] as const) {
    for (const [name, prop] of Object.entries(GLASS_CC_SOURCE)) {
      it(`${mode} ${name} = ${prop}`, () => {
        expect(GLASS_CC_COLOURS[mode][name]).toBe(cssValue(prop, mode));
      });
    }
    for (const [name, prop] of Object.entries(GLASS_CC_SHARED_SOURCE)) {
      it(`${mode} ${name} = ${prop}`, () => {
        const css = cssValue(prop, mode);
        const want = name === "--cc-font" || name === "--cc-mono" ? noHandle(css) : css;
        expect(GLASS_CC_SHARED[name]).toBe(want);
      });
    }
  }
});

describe("the glass block through appTokens", () => {
  it("keeps every --cc-* name, in the same order", () => {
    for (const mode of ["dark", "light"] as const) {
      expect(appTokens(THEME, mode, "glass").map(([n]) => n)).toEqual([...CC_TOKEN_NAMES]);
    }
  });

  it("leaves no var() that the frame cannot resolve", () => {
    const defined = new Set(CC_TOKEN_NAMES);
    for (const mode of ["dark", "light"] as const) {
      for (const [name, value] of appTokens(THEME, mode, "glass")) {
        for (const [, ref] of String(value).matchAll(/var\(\s*(--[a-z-]+)/gi)) {
          expect(defined.has(ref), `${mode} ${name} → ${ref}`).toBe(true);
        }
      }
    }
  });

  it("hands the frame glass colours, corners and a system font", () => {
    const map = appTokenMap(THEME, "dark", "glass");
    expect(map["--cc-card"]).toBe("#1c1c1e");
    expect(map["--cc-primary"]).toBe("#0a84ff");
    expect(map["--cc-radius"]).toBe("0.875rem");
    expect(map["--cc-font"]).toContain("-apple-system");
    expect(map["--cc-font"]).not.toMatch(/var\(/);
  });

  it("keeps the classic value of a name glass does not list", () => {
    const glass = appTokenMap(THEME, "dark", "glass");
    const classic = appTokenMap(THEME, "dark");
    expect(glass["--cc-border-width"]).toBe(classic["--cc-border-width"]);
    expect(glass["--cc-control-label-transform"]).toBe(classic["--cc-control-label-transform"]);
  });
});

describe("classic frames are byte for byte what they were", () => {
  it("gives the same block with no look, and with look classic", () => {
    for (const mode of ["dark", "light"] as const) {
      expect(appTokens(THEME, mode, "classic")).toEqual(appTokens(THEME, mode));
      expect(appTokenCss(THEME, mode, "    ", "classic")).toBe(appTokenCss(THEME, mode));
    }
  });

  it("gives the same srcDoc with no look, and with look classic", () => {
    const a = buildSrcDoc("<p>x</p>", THEME, "dark", {});
    expect(buildSrcDoc("<p>x</p>", THEME, "dark", {}, "classic")).toBe(a);
    expect(buildSrcDoc("<p>x</p>", THEME, "dark", {}, "glass")).not.toBe(a);
    expect(buildSrcDoc("<p>x</p>", THEME, "dark", {}, "glass")).toContain("--cc-card: #1c1c1e;");
  });

  it("reads no look on a server, so a server render is classic", () => {
    expect(documentLook()).toBe("classic");
  });
});
