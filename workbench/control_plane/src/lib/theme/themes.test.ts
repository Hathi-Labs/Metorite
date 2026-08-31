/**
 * Theme manifest integrity.
 *
 * The load-bearing test here is the drift guard, and 2026-08-31 changed which
 * way it points. `globals.css` is now the SOURCE — the browser reads it, there
 * is no generated layer above it — and `themes.ts` is a MIRROR kept for the
 * three consumers that need the tokens as data (the generated-app sandbox,
 * Monaco/Shiki, and the contrast gate). A mirror that silently disagrees with
 * its source is worse than no mirror: a sandboxed app would render one set of
 * colours inside a shell painted with another. This parses the stylesheet and
 * fails if the two ever diverge.
 */

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { THEME } from "./themes";
import { CATEGORICAL_TOKENS, REQUIRED_COLOR_TOKENS } from "./types";
import type { ColorTokens } from "./types";

const GLOBALS = readFileSync(
  fileURLToPath(new URL("../../app/globals.css", import.meta.url)),
  "utf8",
);

/** Custom-property declarations inside the first block matching `selector`. */
function declarationsIn(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`No \`${selector}\` block in globals.css`);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  const body = css.slice(open + 1, close);

  const out: Record<string, string> = {};
  for (const match of body.matchAll(/^\s*(--[\w-]+)\s*:\s*([^;]+);/gm)) {
    out[match[1]] = match[2].trim();
  }
  return out;
}

/** `cardForeground` → `--card-foreground`. */
function cssVar(token: string): string {
  return `--${token.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;
}

describe("the theme is singular", () => {
  it("defines every required colour token in both modes", () => {
    for (const mode of ["dark", "light"] as const) {
      const colors = THEME.colors[mode];
      const missing = REQUIRED_COLOR_TOKENS.filter(
        (token) => !colors[token as keyof ColorTokens],
      );
      expect(missing, mode).toEqual([]);
    }
  });

  it("uses distinct colours for dark and light", () => {
    // Two identical modes is almost always a copy-paste slip.
    expect(THEME.colors.dark.background).not.toBe(THEME.colors.light.background);
  });

  it("carries no icon pack — there is one, and it is Lucide", () => {
    // The engine's last remnant would be an `iconPack` field nothing reads.
    // A field nobody honours is a promise the UI does not keep.
    expect(THEME).not.toHaveProperty("iconPack");
  });
});

describe("the categorical ramp", () => {
  // The ramp is what stops a set of @contexts, tags or chart series from
  // needing raw palette classes. It only works if EVERY slot is present in
  // BOTH modes: a caller writes `text-cat-6` once and it has to resolve in
  // light and dark, or the surface renders with an unresolvable var() — which
  // invalidates the declaration and drops the colour entirely, on half the
  // matrix.
  it("declares every slot in both modes", () => {
    for (const mode of ["dark", "light"] as const) {
      const missing = CATEGORICAL_TOKENS.filter((token) => !THEME.colors[mode][token]);
      expect(missing, mode).toEqual([]);
    }
  });

  it("gives every slot a distinct value in each mode", () => {
    // Two slots sharing a value is a copy-paste slip that defeats the whole
    // point — the two @contexts that hash to them become one colour, and
    // nothing else in the app would ever complain.
    for (const mode of ["dark", "light"] as const) {
      const values = CATEGORICAL_TOKENS.map((token) => THEME.colors[mode][token]);
      expect(new Set(values).size, mode).toBe(CATEGORICAL_TOKENS.length);
    }
  });

  it("re-tints the ramp for light mode rather than reusing dark's", () => {
    // Dark-mode slots are chosen to read on a near-black surface. Reused on a
    // white card they are the unreadable half of this feature, and the
    // contrast gate would catch it — this says WHICH mistake was made.
    for (const token of CATEGORICAL_TOKENS) {
      expect(THEME.colors.light[token], token).not.toBe(THEME.colors.dark[token]);
    }
  });

  it("bridges every slot into Tailwind", () => {
    // `bg-cat-3` exists only because `@theme inline` names it. A slot defined
    // in the manifest but missing here is a class that silently does nothing.
    for (const token of CATEGORICAL_TOKENS) {
      expect(GLOBALS, `--color-${token}`).toContain(`--color-${token}: var(--${token});`);
    }
  });
});

describe("globals.css is the source, and the manifest mirrors it", () => {
  it("mirrors every dark colour token in :root", () => {
    const root = declarationsIn(GLOBALS, ":root");
    for (const [token, value] of Object.entries(THEME.colors.dark)) {
      expect.soft(root[cssVar(token)], `:root ${cssVar(token)}`).toBe(value);
    }
  });

  it("mirrors every light colour token in .light", () => {
    const light = declarationsIn(GLOBALS, ".light");
    for (const [token, value] of Object.entries(THEME.colors.light)) {
      expect.soft(light[cssVar(token)], `.light ${cssVar(token)}`).toBe(value);
    }
  });

  it("mirrors the shape, effect and typography tokens in :root", () => {
    const root = declarationsIn(GLOBALS, ":root");
    expect.soft(root["--radius"]).toBe(THEME.shape.radius);
    expect.soft(root["--border-width"]).toBe(THEME.shape.borderWidth);
    expect.soft(root["--glass-blur"]).toBe(THEME.effects.glassBlur);
    expect.soft(root["--glass-opacity"]).toBe(THEME.effects.glassOpacity);
    expect.soft(root["--glass-opacity-strong"]).toBe(THEME.effects.glassOpacityStrong);
    expect.soft(root["--glow-strength"]).toBe(THEME.effects.glowStrength);
    expect.soft(root["--elevation"]).toBe(THEME.effects.shadow);
    expect.soft(root["--motion-duration"]).toBe(THEME.effects.motionDuration);
    expect.soft(root["--motion-easing"]).toBe(THEME.effects.motionEasing);
    expect.soft(root["--heading-tracking"]).toBe(THEME.typography.headingLetterSpacing);
    expect.soft(root["--heading-weight"]).toBe(THEME.typography.headingWeight);
    expect.soft(root["--label-weight"]).toBe(THEME.typography.labelWeight);
    expect.soft(root["--button-radius"]).toBe(THEME.controls.buttonRadius);
    expect.soft(root["--control-filled-border"]).toBe(THEME.controls.filledBorderWidth);
    expect.soft(root["--control-state-layer"]).toBe(THEME.controls.stateLayerOpacity);
    expect.soft(root["--control-focus-ring"]).toBe(THEME.controls.focusRingWidth);
    expect.soft(root["--control-label-tracking"]).toBe(THEME.controls.labelTracking);
    expect.soft(root["--control-label-transform"]).toBe(THEME.controls.labelTransform);
  });

  it("mirrors the font stacks, fallbacks included", () => {
    // The fallback half is the part that drifted: globals.css named the
    // webfont handle alone while the manifest carried `handle, system-ui, …`.
    // With no generated layer to overwrite it, whatever is here IS what ships
    // — so if Geist fails to load, this decides whether text falls to the
    // OS's face or to the browser's default serif.
    const root = declarationsIn(GLOBALS, ":root");
    expect.soft(root["--font-app"]).toBe(THEME.typography.app);
    expect.soft(root["--font-app-mono"]).toBe(THEME.typography.mono);
    expect
      .soft(root["--font-display"])
      .toBe(THEME.typography.display ?? THEME.typography.app);
  });

  it("still declares the Tailwind bridge the tokens feed", () => {
    // Every colour utility resolves through `@theme inline`; losing an entry
    // there silently drops a whole family of classes.
    expect(GLOBALS).toContain("--color-primary: var(--primary)");
    expect(GLOBALS).toContain("--font-sans: var(--font-app)");
    expect(GLOBALS).toContain("--radius-lg: var(--radius)");
  });

  it("scopes the tokens to :root and .light, not to a theme attribute", () => {
    // The engine's selector was `html[data-theme="…"]`. One left behind would
    // out-specify `:root` and pin the app to a theme nothing can change.
    // Matched in its SELECTOR form: the file's own header explains that the
    // attribute is gone, and a fence that trips on its own rationale is a
    // fence against documentation.
    expect(GLOBALS).not.toMatch(/\[data-theme/);
  });
});

describe("third-party surface themes", () => {
  // Monaco silently falls back to its default for an unknown id, and Shiki
  // throws at render time — neither surfaces a typo anywhere a type-checker or
  // a page load would catch it. So the names are checked against reality here.
  const MONACO_BUILT_INS = new Set(["vs", "vs-dark", "hc-black", "hc-light"]);

  it("names Monaco themes that exist", () => {
    for (const mode of ["dark", "light"] as const) {
      expect(
        MONACO_BUILT_INS.has(THEME.surfaces.monaco[mode]),
        `${mode} → ${THEME.surfaces.monaco[mode]}`,
      ).toBe(true);
    }
  });

  it("names Shiki themes that Shiki actually bundles", () => {
    for (const mode of ["dark", "light"] as const) {
      const name = THEME.surfaces.shiki[mode];
      expect(
        existsSync(
          fileURLToPath(
            new URL(`../../../node_modules/@shikijs/themes/dist/${name}.mjs`, import.meta.url),
          ),
        ),
        `${mode} → ${name} is not a bundled Shiki theme`,
      ).toBe(true);
    }
  });

  it("gives the dark and light modes different surface themes", () => {
    // Reusing one highlighting theme for both modes renders dark code on a
    // light page, or vice versa.
    expect(THEME.surfaces.shiki.dark).not.toBe(THEME.surfaces.shiki.light);
    expect(THEME.surfaces.monaco.dark).not.toBe(THEME.surfaces.monaco.light);
  });
});
