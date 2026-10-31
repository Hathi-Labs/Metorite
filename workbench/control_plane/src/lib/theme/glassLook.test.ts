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

  it("keep every glass-only token out of every classic block", () => {
    // Every block whose selector is exactly `:root` or `.light`, not only the
    // first: the scrollbars open a second `:root` block further down.
    const heads = [...GLOBALS.matchAll(/(^|\n)[ \t]*(:root|\.light)[ \t]*\{/g)];
    expect(heads.length).toBeGreaterThanOrEqual(3);
    for (const h of heads) {
      const body = block(GLOBALS.slice(h.index), `${h[2]}`);
      for (const token of GLASS_ONLY_TOKENS) {
        expect.soft(body[token], `${h[2]} block at ${h.index}: ${token}`).toBeUndefined();
      }
    }
  });

  it("hand reduced motion the glass durations too", () => {
    // The glass block out-specifies the `:root` of the reduce block, so the
    // reduce block must name glass itself (review of GL-1, P1).
    const reduce = GLOBALS.indexOf("@media (prefers-reduced-motion: reduce) {", GLOBALS.indexOf("── Reduced motion"));
    expect(reduce).toBeGreaterThan(-1);
    const glass = block(GLOBALS.slice(reduce), DARK);
    expect(glass["--motion-duration"]).toBe("0.01ms");
    expect(glass["--sheet-duration"]).toBe("0.01ms");
    expect(glass["--spring-duration"]).toBe("0.01ms");
  });

  it("leave the focus ring at full strength", () => {
    // A half-strength halo measured under 3 : 1 on every glass surface
    // (WCAG 1.4.11). Glass keeps the primitives' own ring.
    expect(GLOBALS).not.toMatch(/data-look="glass"\][^{]*:focus-visible[^{]*\{[^}]*color-mix/);
  });

  it("restate in the light block every colour that classic light owns", () => {
    // The dark glass block matches <html> in light mode too, and it
    // out-specifies classic `.light`. So a token that glass sets in dark and
    // classic sets in light must be restated in the glass light block, or
    // the dark value shows in light. The accent pair is the one exception:
    // it is var(--primary), which resolves to the light tint there.
    const dark = block(GLOBALS, DARK);
    const light = block(GLOBALS, LIGHT);
    const classicLight = block(GLOBALS, ".light {");
    const leaks = Object.keys(dark).filter(
      (t) => t in classicLight && !(t in light) && !["--accent", "--accent-foreground"].includes(t),
    );
    expect(leaks).toEqual([]);
  });

  it("make the accent follow the tint", () => {
    // A member's or an organization's accent sets --primary inline. A fixed
    // --accent would leave a second blue beside it.
    const dark = block(GLOBALS, DARK);
    expect(dark["--accent"]).toBe("var(--primary)");
    expect(dark["--accent-foreground"]).toBe("var(--primary-foreground)");
    expect(block(GLOBALS, LIGHT)["--accent"]).toBeUndefined();
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
const COLOUR_PREFIX =
  "(bg|text|border(-[xytrblse])?|ring|ring-offset|outline|divide|fill|stroke|from|via|to|decoration|caret|accent|placeholder|shadow|inset-shadow)";

const GLASS_ONLY_CLASS = new RegExp(
  "^(" +
    // Every glass material and every glass motion utility (GL-1, GL-4).
    "glass-[a-z0-9-]+" +
    // A glass-only colour after ANY colour prefix.
    `|${COLOUR_PREFIX}-(system-[a-z]+|grouped|field|segment)` +
    "|shadow-card|shadow-glass" +
    "|text-(caption|footnote|label|subhead|callout|body|headline|title|title-lg|largetitle)" +
    ")$",
);

/** The utility of a class token: no variants, no `!`, no `/opacity` or `/line-height` modifier. */
function utilityOf(token: string): string {
  const parts = token.split(":");
  return parts[parts.length - 1].replace(/^!|!$/g, "").replace(/\/.*$/, "");
}

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
        if (!GLASS_ONLY_CLASS.test(utilityOf(token))) continue;
        if (token.split(":").slice(0, -1).includes("glass")) continue;
        offenders.push(`${rel}: ${token}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("recognise the forms a later slice could slip past it", () => {
    // Review of GL-1, P1: each of these escaped the first regex.
    for (const bare of [
      "text-body!",
      "!text-body",
      "bg-field/80",
      "text-body/6",
      "bg-system-blue/[0.2]",
      "outline-system-blue",
      "from-system-pink",
      "text-field",
      "border-segment",
      "border-t-grouped",
      "glass-working-glow",
      "hover:glass-chrome",
    ]) {
      expect.soft(GLASS_ONLY_CLASS.test(utilityOf(bare)), bare).toBe(true);
    }
    for (const fine of ["glass:text-body!", "md:glass:bg-field/80", "glass:hover:bg-system-blue"]) {
      expect.soft(fine.split(":").slice(0, -1).includes("glass"), fine).toBe(true);
    }
    for (const classic of ["text-sm", "bg-card", "tech-glass", "text-primary", "bg-secondary/50", "border-border"]) {
      expect.soft(GLASS_ONLY_CLASS.test(utilityOf(classic)), classic).toBe(false);
    }
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

/** A tone at `pct` percent over an opaque surface: the 10 % chip of `statusAccent.ts`. */
function tint(tone: Rgba, pct: number, under: Rgba): Rgba {
  return over({ ...tone, a: pct / 100 }, under);
}

const AA = 4.5;

/**
 * The pairs that carry text, mirroring `contrast.test.ts` for classic
 * (DESIGN_SYSTEM.md §7), plus the glass surfaces. A translucent background
 * is blended over each opaque surface it can sit on (`under`), and the pair
 * must pass on every one of them.
 *
 * Glass measures the tint AS TEXT at 4.5, where classic asks 3.0: Aroh's
 * rule is that text in the tint passes 4.5 on every surface.
 */
type Pair = { fg: string; bg: string; under?: string[]; min: number; chip?: boolean };
const SURFACES = ["--background", "--card"];
const STATUS = ["--destructive", "--success", "--warning", "--info", "--violet"];
const CATEGORICAL = Array.from({ length: 12 }, (_, i) => `--cat-${i + 1}`);

const PAIRS: Pair[] = [
  { fg: "--foreground", bg: "--background", min: AA },
  { fg: "--card-foreground", bg: "--card", min: AA },
  { fg: "--popover-foreground", bg: "--popover", min: AA },
  { fg: "--tooltip-foreground", bg: "--tooltip", min: AA },
  { fg: "--muted-foreground", bg: "--background", min: AA },
  { fg: "--muted-foreground", bg: "--card", min: AA },
  { fg: "--primary-foreground", bg: "--primary", min: AA },
  { fg: "--secondary-foreground", bg: "--secondary", under: SURFACES, min: AA },
  { fg: "--foreground", bg: "--muted", under: SURFACES, min: AA },
  { fg: "--accent-foreground", bg: "--accent", min: AA },
  { fg: "--sidebar-primary-foreground", bg: "--sidebar-primary", min: AA },
  { fg: "--sidebar-foreground", bg: "--sidebar-background", under: SURFACES, min: AA },
  { fg: "--primary", bg: "--background", min: AA },
  { fg: "--primary", bg: "--card", min: AA },
  ...STATUS.flatMap((tone): Pair[] => [
    { fg: `${tone}-foreground`, bg: tone, min: AA },
    { fg: tone, bg: "--background", min: AA },
    { fg: tone, bg: "--card", min: AA },
    { fg: tone, bg: "--muted", under: SURFACES, min: AA },
    { fg: tone, bg: tone, under: ["--card"], min: AA, chip: true },
  ]),
  ...CATEGORICAL.flatMap((cat): Pair[] => SURFACES.map((bg) => ({ fg: cat, bg, min: AA }))),
];

/** The glass tokens of one mode, with classic beneath for what glass does not redefine. */
function glassTokens(mode: "dark" | "light"): Record<string, string> {
  const classic = mode === "dark" ? block(GLOBALS, ":root {") : { ...block(GLOBALS, ":root {"), ...block(GLOBALS, ".light {") };
  return { ...classic, ...block(GLOBALS, DARK), ...(mode === "light" ? block(GLOBALS, LIGHT) : {}) };
}

/** A token's colour, following `var(--x)` within the same token set. */
function colourOf(tokens: Record<string, string>, name: string): Rgba {
  let value = tokens[name];
  for (let hop = 0; value && hop < 5; hop++) {
    const ref = value.match(/^var\((--[\w-]+)\)$/);
    if (!ref) break;
    value = tokens[ref[1]];
  }
  if (!value) throw new Error(`No value for ${name}`);
  return rgba(value);
}

describe("glass contrast", () => {
  for (const mode of ["dark", "light"] as const) {
    const tokens = glassTokens(mode);
    for (const pair of PAIRS) {
      const unders = pair.under ?? [null];
      for (const under of unders) {
        const label = `${mode}: ${pair.fg} on ${pair.chip ? `its 10% chip` : pair.bg}${under ? ` over ${under}` : ""} ≥ ${pair.min}`;
        it(label, () => {
          let bg = colourOf(tokens, pair.bg);
          if (pair.chip) bg = tint(bg, 10, colourOf(tokens, under!));
          else if (bg.a < 1) bg = over(bg, colourOf(tokens, under ?? "--background"));
          const fg = over(colourOf(tokens, pair.fg), bg);
          const ratio = contrastRatio(fg, bg);
          expect(ratio, `${label}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(pair.min);
        });
      }
    }
  }

  it("measures with the same maths as the classic gate", () => {
    // White on black is 21 : 1. If this drifts, the pairs above mean nothing.
    expect(contrastRatio(rgba("#ffffff"), rgba("#000000"))).toBeCloseTo(21, 5);
    expect(luminance(rgba("#ffffff"))).toBeCloseTo(1, 5);
  });
});
