/**
 * The theme — singular, since 2026-08-31.
 *
 * This file used to hold four manifests and a registry, and adding a fifth
 * was a supported operation. The owner retired that engine: there is one
 * look, it is the Control Plane's original, and every app renders in it.
 *
 * What replaced the engine is nothing at all. The browser reads
 * `globals.css`, which carries these same values in its `:root` / `.light`
 * blocks. No `data-theme` attribute, no generated stylesheet, no runtime
 * switch. See `THEME` at the foot of the file for who still needs the
 * values as DATA rather than as CSS.
 *
 * Font stacks reference `var(--font-*)` handles registered by `next/font` in
 * `src/app/layout.tsx`, and name a platform fallback after them so text
 * still renders if a webfont never arrives.
 */

import type { Theme } from "./types";

const GEIST = "var(--font-geist-sans)";
const GEIST_MONO = "var(--font-geist-mono)";

const SYSTEM_FALLBACK = "system-ui, -apple-system, BlinkMacSystemFont, sans-serif";
const MONO_FALLBACK = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

/**
 * The categorical ramp — `--cat-1` … `--cat-12`.
 *
 * The theme carries all twelve slots in both modes, in one hue sequence, so
 * a slot means the same family everywhere (cat-3 is always the green one).
 * The saturation is this theme's own vivid register.
 *
 *   slot  1     2      3      4       5     6     7       8
 *   hue   215   27     142    264     358   182   324     66
 *         blue  orange green  violet  red   teal  magenta olive
 *
 *   slot  9     10      11      12
 *   hue   104   240     292     46
 *         lime  indigo  purple  amber
 *
 * Slots 9–12 (2026-08-31) are CHOICE-ONLY: `hashSlot` never lands on them
 * (its modulus is frozen at 8 — see `HASH_SLOTS`), so nothing already
 * assigned repaints. They exist for the hand-picked case, a space's marker
 * in Space Settings, where the owner asked for more choices. The min-ΔE
 * claim below was measured for the FIRST EIGHT; the four additions sit in
 * the wheel's largest gaps but weaken worst-pair separation, which is the
 * accepted price of choice. Contrast (AA on card and background) is still
 * measured for all twelve — `contrast.test.ts` iterates the token list.
 *
 * The hues are not evenly spaced round the wheel, because even spacing in HSL
 * degrees is not even spacing to an eye: they were picked by maximising the
 * WORST pairwise CIE-Lab separation. ⚠️ That optimisation ran across the four
 * themes this file used to hold, and the hardest case it solved for —
 * Graphite dark, the least saturated — is gone. The hues are kept anyway:
 * they are load-bearing (a reorder repaints every context) and they score
 * BETTER here than in the case they were chosen for.
 *
 * Lightness is picked per mode, per hue, so every slot clears WCAG AA
 * (≥ 4.5:1) against `card` AND `background`, with headroom. Equal-contrast
 * rather than equal-lightness is why the greens and olives sit lower on the
 * HSL scale than the blues — they are intrinsically more luminous, and
 * matching HSL lightness would have made them the two slots nobody can read.
 * `contrast.test.ts` measures every pair.
 *
 * One honest limit, measured: under simulated deuteranopia the eight collapse
 * to about four — 1/4 (blue/violet), 2/8 (orange/olive) and 6/7 (teal/magenta)
 * each merge. No eight-hue qualitative palette survives dichromacy; the
 * colourblind-safe ceiling is around five. So a `--cat-*` slot must never be
 * the ONLY thing carrying a distinction (DESIGN_SYSTEM §7). Every shipped use
 * pairs it with the label it colours — the @context chip prints the context
 * name, the facet dot sits beside its own text — and any new one has to.
 *
 * Changing a value is fine. Changing the ORDER is not: `contextColors.ts`
 * hashes a @context name to a slot, so a reorder silently repaints every
 * context in the product.
 */

/**
 * The Control Plane's original look, preserved token-for-token.
 *
 * These values are the contract with `globals.css`: the `:root` / `.light`
 * blocks there are what the browser reads, and must stay in sync. A unit
 * test (`themes.test.ts`) parses that stylesheet and fails if the two drift.
 */
const rapidtool: Theme = {
  id: "rapidtool",
  name: "RapidTool",
  description: "The Metorite original — deep blue-gray surfaces, professional blue, soft glass.",
  typography: {
    app: `${GEIST}, ${SYSTEM_FALLBACK}`,
    mono: `${GEIST_MONO}, ${MONO_FALLBACK}`,
    headingLetterSpacing: "0em",
    headingWeight: "700",
    labelWeight: "500",
  },
  shape: { radius: "0.75rem", borderWidth: "1px" },
  effects: {
    glassBlur: "16px",
    glassOpacity: "0.65",
    glassOpacityStrong: "0.92",
    glowStrength: "1",
    shadow: "0 4px 16px hsl(0 0% 0% / 0.25)",
    motionDuration: "0.2s",
    motionEasing: "cubic-bezier(0.25, 0.46, 0.45, 0.94)",
  },
  controls: {
    buttonRadius: "var(--radius)",
    filledBorderWidth: "0px",
    stateLayerOpacity: "0",
    focusRingWidth: "2px",
    labelTracking: "0em",
    labelTransform: "none",
  },
  surfaces: {
    monaco: { dark: "vs-dark", light: "vs" },
    shiki: { dark: "github-dark", light: "github-light" },
  },
  colors: {
    dark: {
      background: "hsl(220 13% 8%)",
      foreground: "hsl(210 40% 98%)",
      card: "hsl(220 13% 10%)",
      cardForeground: "hsl(210 40% 98%)",
      popover: "hsl(220 13% 12%)",
      popoverForeground: "hsl(210 40% 98%)",
      primary: "hsl(198 89% 50%)",
      primaryForeground: "hsl(220 13% 8%)",
      secondary: "hsl(220 13% 14%)",
      secondaryForeground: "hsl(210 40% 98%)",
      muted: "hsl(220 13% 15%)",
      mutedForeground: "hsl(215 20% 65%)",
      accent: "hsl(27 96% 61%)",
      accentForeground: "hsl(220 13% 8%)",
      destructive: "hsl(0 63% 60%)",
      destructiveForeground: "hsl(210 40% 98%)",
      border: "hsl(220 13% 16%)",
      input: "hsl(220 13% 16%)",
      ring: "hsl(198 89% 50%)",
      success: "hsl(142 76% 47%)",
      successForeground: "hsl(220 13% 8%)",
      warning: "hsl(47 96% 53%)",
      warningForeground: "hsl(220 13% 8%)",
      info: "hsl(198 89% 55%)",
      infoForeground: "hsl(220 13% 8%)",
      violet: "hsl(268 80% 68%)",
      violetForeground: "hsl(220 13% 8%)",
      // Categorical ramp — vivid, matching RapidTool's saturated register.
      "cat-1": "hsl(215 85% 61%)",
      "cat-2": "hsl(27 85% 47%)",
      "cat-3": "hsl(142 85% 35%)",
      "cat-4": "hsl(264 85% 70%)",
      "cat-5": "hsl(358 85% 64%)",
      "cat-6": "hsl(182 85% 34%)",
      "cat-7": "hsl(324 85% 61%)",
      "cat-8": "hsl(66 85% 32%)",
      "cat-9": "hsl(104 85% 33%)",
      "cat-10": "hsl(240 85% 70%)",
      "cat-11": "hsl(292 85% 64%)",
      "cat-12": "hsl(46 85% 38%)",
      sidebarBackground: "hsl(220 13% 9%)",
      sidebarForeground: "hsl(210 40% 98%)",
      sidebarPrimary: "hsl(198 89% 50%)",
      sidebarPrimaryForeground: "hsl(220 13% 8%)",
      sidebarAccent: "hsl(220 13% 13%)",
      sidebarAccentForeground: "hsl(210 40% 98%)",
      sidebarBorder: "hsl(220 13% 16%)",
      sidebarRing: "hsl(198 89% 50%)",
    },
    light: {
      background: "hsl(0 0% 100%)",
      foreground: "hsl(222.2 84% 4.9%)",
      card: "hsl(0 0% 100%)",
      cardForeground: "hsl(222.2 84% 4.9%)",
      popover: "hsl(0 0% 100%)",
      popoverForeground: "hsl(222.2 84% 4.9%)",
      primary: "hsl(198 89% 35%)",
      primaryForeground: "hsl(0 0% 100%)",
      secondary: "hsl(210 40% 96%)",
      secondaryForeground: "hsl(222.2 84% 4.9%)",
      muted: "hsl(210 40% 96%)",
      mutedForeground: "hsl(215.4 16.3% 46.9%)",
      accent: "hsl(27 96% 61%)",
      accentForeground: "hsl(210 40% 98%)",
      destructive: "hsl(0 84.2% 60.2%)",
      destructiveForeground: "hsl(210 40% 98%)",
      border: "hsl(214.3 31.8% 91.4%)",
      input: "hsl(214.3 31.8% 91.4%)",
      ring: "hsl(198 89% 50%)",
      success: "hsl(142 76% 47%)",
      successForeground: "hsl(210 40% 98%)",
      warning: "hsl(47 96% 53%)",
      warningForeground: "hsl(210 40% 98%)",
      info: "hsl(198 89% 38%)",
      infoForeground: "hsl(210 40% 98%)",
      violet: "hsl(268 70% 50%)",
      violetForeground: "hsl(210 40% 98%)",
      "cat-1": "hsl(215 85% 47%)",
      "cat-2": "hsl(27 85% 36%)",
      "cat-3": "hsl(142 85% 26%)",
      "cat-4": "hsl(264 85% 59%)",
      "cat-5": "hsl(358 85% 45%)",
      "cat-6": "hsl(182 85% 26%)",
      "cat-7": "hsl(324 85% 43%)",
      "cat-8": "hsl(66 85% 24%)",
      "cat-9": "hsl(104 85% 25%)",
      "cat-10": "hsl(240 85% 56%)",
      "cat-11": "hsl(292 85% 42%)",
      "cat-12": "hsl(46 85% 28%)",
      sidebarBackground: "hsl(0 0% 98%)",
      sidebarForeground: "hsl(222.2 84% 4.9%)",
      sidebarPrimary: "hsl(198 89% 45%)",
      sidebarPrimaryForeground: "hsl(210 40% 98%)",
      sidebarAccent: "hsl(210 40% 95%)",
      sidebarAccentForeground: "hsl(222.2 84% 4.9%)",
      sidebarBorder: "hsl(214.3 31.8% 91.4%)",
      sidebarRing: "hsl(198 89% 50%)",
    },
  },
};

/**
 * THE theme. Not "the default theme" — the only one.
 *
 * Owner directive 2026-08-31: the theming engine is retired, and every app
 * and sub-app renders in the Control Plane's original look. `Fluent`,
 * `Material` and `Graphite` were deleted in the same change, along with the
 * `data-theme` attribute, the icon-pack switch and the theme picker.
 *
 * It stays a MANIFEST rather than becoming loose constants because three
 * consumers need the token values as data, not as CSS:
 *
 *   • `sandbox-frame.ts` builds the `--cc-*` block for generated apps, which
 *     run in an opaque-origin iframe and inherit no stylesheet of ours. Those
 *     names are a published interface — shipped apps we cannot edit read them.
 *   • `surfaces.ts` maps to Monaco's and Shiki's own theme vocabularies, which
 *     cannot be driven by custom properties.
 *   • `contrast.test.ts` measures every token pair against WCAG AA.
 *
 * ⚠️ `globals.css` is the SOURCE for the running app — its `:root` / `.light`
 * blocks are what the browser actually reads. This manifest must agree with
 * it token-for-token, and `themes.test.ts` parses the stylesheet and fails if
 * the two drift. Change a colour in both, or not at all.
 */
export const THEME: Theme = rapidtool;
