/**
 * The glass look's fences (D141, `project-docs/specs/glass_look.md` §6).
 *
 * Glass ships beside classic for a bounded time, and production must keep
 * classic byte for byte until the owner says otherwise. These tests hold
 * the four rules that make that true:
 *
 *   1. The gate decides. With NEXT_PUBLIC_UI_LOOK unset, nothing resolves to
 *      glass and the boot script holds no look code at all.
 *   2. The glass token blocks come after the classic ones, and the classic
 *      blocks hold no glass-only token. `themes.test.ts` holds the classic
 *      values themselves.
 *   3. A glass-only class appears in a component only behind the `glass:`
 *      variant, so it matches nothing under classic.
 *   4. The glass text pairs pass contrast, measured on the CSS itself, with
 *      each translucent fill blended over the surface it sits on.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { themeBootScript } from "./boot";
import { contrastRatio, luminance, parseColor } from "./contrast";
import { glassGateOn, resolveLook } from "./look";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const GLOBALS = readFileSync(join(SRC, "app", "globals.css"), "utf8");

const DARK = 'html[data-look="glass"] {';
const LIGHT = 'html[data-look="glass"].light {';

/** Custom-property declarations inside the first block that starts with `head`. */
function block(css: string, head: string): Record<string, string> {
  const start = css.indexOf(head);
  if (start === -1) throw new Error(`No \`${head}\` block in globals.css`);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  const out: Record<string, string> = {};
  for (const m of css.slice(open + 1, close).matchAll(/^\s*(--[\w-]+)\s*:\s*([^;]+);/gm)) {
    out[m[1]] = m[2].trim();
  }
  return out;
}

/** Tokens glass adds that classic does not define. Each has a fallback in `@theme inline`. */
const GLASS_ONLY_TOKENS = [
  "--background-grouped",
  "--field",
  "--segment",
  "--card-shadow",
  "--glass-bg",
  "--glass-edge",
  "--glass-highlight",
  "--glass-shadow",
  "--glass-thick-bg",
  "--glass-radius-sm",
  "--glass-radius-md",
  "--glass-radius-xl",
  "--glass-radius-2xl",
  "--glass-radius-3xl",
  ...["blue", "indigo", "purple", "pink", "red", "orange", "yellow", "green", "teal", "gray"].map(
    (c) => `--system-${c}`,
  ),
  ...[1, 2, 3, 4, 5].map((n) => `--glow-${n}`),
];

afterEach(() => {
  vi.unstubAllEnvs();
});

// ── 1. The gate ──────────────────────────────────────────────────────────────

describe("the look gate", () => {
  it("is off unless the build says glass", () => {
    vi.stubEnv("NEXT_PUBLIC_UI_LOOK", "");
    expect(glassGateOn()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_UI_LOOK", "classic");
    expect(glassGateOn()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_UI_LOOK", "Glass");
    expect(glassGateOn()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_UI_LOOK", "glass");
    expect(glassGateOn()).toBe(true);
  });

  it("resolves every stored choice to classic when the gate is off", () => {
    // A browser that chose glass on staging must still get classic on
    // production, where the gate is off.
    vi.stubEnv("NEXT_PUBLIC_UI_LOOK", "");
    for (const stored of [null, "glass", "classic", "anything"]) {
      expect(resolveLook(stored)).toBe("classic");
    }
  });

  it("defaults to glass with the gate on, and honours Classic", () => {
    vi.stubEnv("NEXT_PUBLIC_UI_LOOK", "glass");
    expect(resolveLook(null)).toBe("glass");
    expect(resolveLook("glass")).toBe("glass");
    expect(resolveLook("classic")).toBe("classic");
  });

  it("puts no look code in the boot script when the gate is off", () => {
    vi.stubEnv("NEXT_PUBLIC_UI_LOOK", "");
    const script = themeBootScript();
    expect(script).not.toContain("data-look");
    expect(script).not.toContain("cc-look");
  });

  it("lets the boot script remove the look for a member who chose Classic", () => {
    vi.stubEnv("NEXT_PUBLIC_UI_LOOK", "glass");
    const script = themeBootScript();
    expect(script).toContain('"cc-look"');
    expect(script).toContain('removeAttribute("data-look")');
    // It only ever REMOVES the look. The server renders it, so a script that
    // could add it would be a second way in.
    expect(script).not.toContain("setAttribute");
  });

  it("runs the boot script with no error in both gate states", () => {
    for (const gate of ["", "glass"]) {
      vi.stubEnv("NEXT_PUBLIC_UI_LOOK", gate);
      // The script swallows its own errors, so a syntax error is the only
      // way it can fail. `new Function` parses it.
      expect(() => new Function(themeBootScript())).not.toThrow();
    }
  });
});

// ── 2. The token blocks ──────────────────────────────────────────────────────

describe("the glass token blocks", () => {
  it("come after the classic :root and .light blocks", () => {
    // themes.test.ts reads the FIRST `:root {` and `.light {` as classic.
    // `html[data-look="glass"].light {` also contains `.light {`, so a glass
    // block placed first would be read as classic.
    const classicLight = GLOBALS.indexOf(".light {");
    expect(GLOBALS.indexOf(":root {")).toBeLessThan(GLOBALS.indexOf(DARK));
    expect(classicLight).toBeLessThan(GLOBALS.indexOf(DARK));
    expect(GLOBALS.slice(classicLight - 30, classicLight)).not.toContain("data-look");
  });

  it("keep every glass-only token out of the classic blocks", () => {
    const root = block(GLOBALS, ":root {");
    const light = block(GLOBALS, ".light {");
    for (const token of GLASS_ONLY_TOKENS) {
      expect.soft(root[token], `:root ${token}`).toBeUndefined();
      expect.soft(light[token], `.light ${token}`).toBeUndefined();
    }
  });

  it("define only tokens that classic defines, or a listed glass-only token", () => {
    const known = new Set([
      ...Object.keys(block(GLOBALS, ":root {")),
      ...GLASS_ONLY_TOKENS,
    ]);
    for (const head of [DARK, LIGHT]) {
      for (const token of Object.keys(block(GLOBALS, head))) {
        expect.soft(known.has(token), `${head} ${token}`).toBe(true);
      }
    }
  });

  it("give each glass-only colour a classic fallback in the Tailwind bridge", () => {
    for (const token of GLASS_ONLY_TOKENS.filter((t) => /^--(system-|background-grouped|field|segment)/.test(t))) {
      expect.soft(GLOBALS, token).toMatch(new RegExp(`var\\(${token}, var\\(--`));
    }
  });
});

// ── 3. Glass-only classes stay behind the variant ────────────────────────────

/** Every `.ts`/`.tsx` under `src/`, tests excluded, as posix paths. */
function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(SRC, full).split(sep).join("/"));
      }
    }
  };
  walk(SRC);
  return out.sort();
}

/**
 * The class names that exist only for glass. Under classic their tokens fall
 * back, but the SHAPE they draw (a capsule, a blur, an iOS type size) is
 * still glass, so they must not apply there.
 */
const GLASS_ONLY_CLASS =
  /^(glass-(chrome|menu|surface|press|scroll-edge)|bg-grouped|bg-field|bg-segment|(bg|text|border|ring|fill|stroke)-system-[a-z]+(\/\d+)?|shadow-card|shadow-glass|text-(caption|footnote|label|subhead|callout|body|headline|title|title-lg|largetitle))$/;

/**
 * Files that render ONLY under glass, so their classes need no variant. A file
 * goes here only when its component is never mounted in classic, and the
 * comment says where that is decided.
 */
const GLASS_ONLY_FILES: string[] = [];

describe("glass-only classes", () => {
  it("appear only behind the glass: variant", () => {
    const offenders: string[] = [];
    for (const rel of sourceFiles()) {
      if (GLASS_ONLY_FILES.includes(rel)) continue;
      const text = readFileSync(join(SRC, rel), "utf8");
      // A class token: variants joined by ':' and then the utility.
      for (const m of text.matchAll(/[!\w:/\[\].-]+/g)) {
        const token = m[0];
        const parts = token.split(":");
        const utility = parts[parts.length - 1].replace(/^!/, "");
        if (!GLASS_ONLY_CLASS.test(utility)) continue;
        if (parts.slice(0, -1).includes("glass")) continue;
        offenders.push(`${rel}: ${token}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

// ── 4. Contrast ──────────────────────────────────────────────────────────────

type Rgba = { r: number; g: number; b: number; a: number };

/** A colour with its alpha. `parseColor` drops the alpha, so it is read here. */
function rgba(value: string): Rgba {
  const base = parseColor(value);
  if (!base) throw new Error(`Cannot parse ${value}`);
  const alpha = value.match(/\/\s*([\d.]+)\s*\)$/);
  return { ...base, a: alpha ? Number(alpha[1]) : 1 };
}

/** `top` laid over an opaque `under`. */
function over(top: Rgba, under: Rgba): Rgba {
  return {
    r: top.r * top.a + under.r * (1 - top.a),
    g: top.g * top.a + under.g * (1 - top.a),
    b: top.b * top.a + under.b * (1 - top.a),
    a: 1,
  };
}

/**
 * The pairs that carry text. `on` names the surface, and `under` the opaque
 * surface below a translucent one. AA is 4.5 for text. A filled control's
 * label is 3.0, as Apple's own dark-mode buttons are (white on #0a84ff).
 */
const PAIRS: { fg: string; bg: string; under?: string; min: number }[] = [
  { fg: "--foreground", bg: "--background", min: 4.5 },
  { fg: "--foreground", bg: "--card", min: 4.5 },
  { fg: "--popover-foreground", bg: "--popover", min: 4.5 },
  { fg: "--tooltip-foreground", bg: "--tooltip", min: 4.5 },
  { fg: "--muted-foreground", bg: "--background", min: 4.5 },
  { fg: "--muted-foreground", bg: "--card", min: 4.5 },
  { fg: "--foreground", bg: "--secondary", under: "--card", min: 4.5 },
  { fg: "--foreground", bg: "--muted", under: "--card", min: 4.5 },
  { fg: "--primary", bg: "--background", min: 4.5 },
  { fg: "--primary", bg: "--card", min: 4.5 },
  { fg: "--primary-foreground", bg: "--primary", min: 3.0 },
  ...["--destructive", "--success", "--warning", "--info", "--violet"].flatMap((tone) => [
    { fg: tone, bg: "--background", min: 4.5 },
    { fg: tone, bg: "--card", min: 4.5 },
    { fg: `${tone}-foreground`, bg: tone, min: 4.5 },
  ]),
  { fg: "--sidebar-foreground", bg: "--sidebar-background", under: "--background", min: 4.5 },
];

describe("glass contrast", () => {
  for (const [mode, head] of [
    ["dark", DARK],
    ["light", LIGHT],
  ] as const) {
    // Light inherits what it does not redefine from dark.
    const tokens = { ...block(GLOBALS, DARK), ...(mode === "light" ? block(GLOBALS, LIGHT) : {}) };
    for (const pair of PAIRS) {
      it(`${mode}: ${pair.fg} on ${pair.bg} ≥ ${pair.min}`, () => {
        let bg = rgba(tokens[pair.bg]);
        if (bg.a < 1) bg = over(bg, rgba(tokens[pair.under ?? "--background"]));
        const fg = over(rgba(tokens[pair.fg]), bg);
        const ratio = contrastRatio(fg, bg);
        expect(ratio, `${head} ${pair.fg} on ${pair.bg}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(pair.min);
      });
    }
  }

  it("measures with the same maths as the classic gate", () => {
    // White on black is 21 : 1. If this drifts, the pairs above mean nothing.
    expect(contrastRatio(rgba("#ffffff"), rgba("#000000"))).toBeCloseTo(21, 5);
    expect(luminance(rgba("#ffffff"))).toBeCloseTo(1, 5);
  });
});
