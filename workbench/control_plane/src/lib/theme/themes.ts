/**
 * Built-in themes.
 *
 * Adding a theme means adding an entry here — no component, CSS or Tailwind
 * change is required. `buildThemeCss()` turns each manifest into the
 * `html[data-theme="…"]` custom-property scope the app renders against.
 *
 * Font stacks reference `var(--font-*)` handles registered by `next/font` in
 * `src/app/layout.tsx`; a theme that wants a font nobody has loaded yet must
 * add it there too. Stacks may name platform fonts first (Segoe UI, Cascadia
 * Code) so a theme looks native where the OS can supply the real thing, and
 * falls back to a self-hosted face everywhere else.
 */

import type { Theme } from "./types";

// Shared font handles, so a typo lands in one place rather than four.
const GEIST = "var(--font-geist-sans)";
const GEIST_MONO = "var(--font-geist-mono)";
const INTER = "var(--font-inter)";
const ROBOTO = "var(--font-roboto)";
const ROBOTO_MONO = "var(--font-roboto-mono)";

const SYSTEM_FALLBACK = "system-ui, -apple-system, BlinkMacSystemFont, sans-serif";
const MONO_FALLBACK = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

/**
 * The categorical ramp — `--cat-1` … `--cat-12`.
 *
 * Every theme below carries all twelve slots in both modes. They share one hue
 * sequence so a slot means the same *family* everywhere (cat-3 is always the
 * green one), and differ in saturation so the ramp belongs to its theme:
 * RapidTool vivid, Fluent clean, Material softened toward M3's tonal
 * palettes, Graphite muted to stay inside its near-monochrome register.
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
 * WORST pairwise CIE-Lab separation across all eight theme/mode combinations
 * (min ΔE ≈ 27 on Graphite dark, the least saturated and therefore hardest
 * case — evenly spaced sets scored ≈ 20 there).
 *
 * Lightness is NOT shared: it is picked per theme, per mode, per hue so every
 * slot clears WCAG AA (≥ 4.5:1) against that theme's own `card` AND
 * `background`, with headroom. Equal-contrast rather than equal-lightness is
 * why the greens and olives sit lower on the HSL scale than the blues — they
 * are intrinsically more luminous, and matching HSL lightness would have made
 * them the two slots nobody can read. `contrast.test.ts` measures all 64.
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
 * RapidTool — the Control Plane's original look, preserved token-for-token.
 *
 * These values are the contract with `globals.css`: the `:root` / `.light`
 * blocks there are the no-JavaScript fallback and must stay in sync. A unit
 * test (`themes.test.ts`) parses that stylesheet and fails if the two drift.
 */
const rapidtool: Theme = {
  id: "rapidtool",
  name: "RapidTool",
  description: "The Metorite original — deep blue-gray surfaces, professional blue, soft glass.",
  iconPack: "lucide",
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
 * Fluent — Microsoft's design language (Fluent 2, the Windows 11 / Lumia
 * lineage). Near-square corners, Segoe UI where the OS has it, acrylic
 * instead of glow, and the Fluent System Icons pack.
 */
const fluent: Theme = {
  id: "fluent",
  name: "Fluent",
  description: "Microsoft's Fluent 2 — squared corners, Segoe UI, acrylic surfaces, Fluent icons.",
  inspiration: "Microsoft Fluent 2 design system",
  iconPack: "fluent",
  typography: {
    app: `"Segoe UI Variable Text", "Segoe UI", ${INTER}, ${SYSTEM_FALLBACK}`,
    mono: `"Cascadia Code", "Cascadia Mono", Consolas, ${GEIST_MONO}, ${MONO_FALLBACK}`,
    display: `"Segoe UI Variable Display", "Segoe UI", ${INTER}, ${SYSTEM_FALLBACK}`,
    headingLetterSpacing: "-0.005em",
    headingWeight: "600",
    labelWeight: "600",
  },
  shape: { radius: "0.25rem", borderWidth: "1px" },
  effects: {
    // Acrylic: a heavier blur over a more opaque surface than our default glass.
    glassBlur: "30px",
    glassOpacity: "0.82",
    glassOpacityStrong: "0.95",
    glowStrength: "0",
    shadow: "0 2px 4px hsl(0 0% 0% / 0.14), 0 0 2px hsl(0 0% 0% / 0.12)",
    motionDuration: "0.15s",
    motionEasing: "cubic-bezier(0.33, 0, 0.67, 1)",
  },
  controls: {
    // Fluent draws a 1px stroke on solid buttons too, which is what stops them
    // reading as flat blocks of colour.
    buttonRadius: "var(--radius)",
    filledBorderWidth: "1px",
    stateLayerOpacity: "0",
    focusRingWidth: "2px",
    labelTracking: "0em",
    labelTransform: "none",
  },
  surfaces: {
    monaco: { dark: "vs-dark", light: "vs" },
    shiki: { dark: "dark-plus", light: "light-plus" },
  },
  colors: {
    dark: {
      background: "hsl(0 0% 13%)",
      foreground: "hsl(0 0% 98%)",
      card: "hsl(0 0% 17%)",
      cardForeground: "hsl(0 0% 98%)",
      popover: "hsl(0 0% 19%)",
      popoverForeground: "hsl(0 0% 98%)",
      // Windows dark-mode accent (#4CC2FF) — the light-mode #0078D4 fails
      // contrast on a near-black surface.
      primary: "hsl(197 100% 65%)",
      primaryForeground: "hsl(0 0% 10%)",
      secondary: "hsl(0 0% 22%)",
      secondaryForeground: "hsl(0 0% 98%)",
      muted: "hsl(0 0% 20%)",
      mutedForeground: "hsl(0 0% 72%)",
      accent: "hsl(263 37% 62%)",
      accentForeground: "hsl(0 0% 10%)",
      destructive: "hsl(353 100% 80%)",
      destructiveForeground: "hsl(0 0% 10%)",
      border: "hsl(0 0% 24%)",
      input: "hsl(0 0% 24%)",
      ring: "hsl(197 100% 65%)",
      success: "hsl(113 51% 59%)",
      successForeground: "hsl(0 0% 10%)",
      warning: "hsl(53 100% 49%)",
      warningForeground: "hsl(0 0% 10%)",
      // Fluent's surfaces are the lightest darks we ship (card is 17%), so the
      // ramp has to sit higher here than anywhere else to clear AA.
      "cat-1": "hsl(215 80% 67%)",
      "cat-2": "hsl(27 80% 56%)",
      "cat-3": "hsl(142 80% 40%)",
      "cat-4": "hsl(264 80% 75%)",
      "cat-5": "hsl(358 80% 71%)",
      "cat-6": "hsl(182 80% 40%)",
      "cat-7": "hsl(324 80% 69%)",
      "cat-8": "hsl(66 80% 37%)",
      "cat-9": "hsl(104 80% 38%)",
      "cat-10": "hsl(240 80% 75%)",
      "cat-11": "hsl(292 80% 70%)",
      "cat-12": "hsl(46 80% 43%)",
      sidebarBackground: "hsl(0 0% 15%)",
      sidebarAccent: "hsl(0 0% 21%)",
      sidebarBorder: "hsl(0 0% 22%)",
    },
    light: {
      background: "hsl(0 0% 95%)",
      foreground: "hsl(0 0% 11%)",
      card: "hsl(0 0% 100%)",
      cardForeground: "hsl(0 0% 11%)",
      popover: "hsl(0 0% 100%)",
      popoverForeground: "hsl(0 0% 11%)",
      // 41% rather than Fluent's nominal 42%: white on 42% measures
      // 4.44:1, a hair under AA. See contrast.test.ts.
      primary: "hsl(206 100% 41%)",
      primaryForeground: "hsl(0 0% 100%)",
      secondary: "hsl(0 0% 92%)",
      secondaryForeground: "hsl(0 0% 11%)",
      muted: "hsl(0 0% 94%)",
      mutedForeground: "hsl(0 0% 38%)",
      accent: "hsl(263 37% 56%)",
      accentForeground: "hsl(0 0% 100%)",
      destructive: "hsl(6 75% 44%)",
      destructiveForeground: "hsl(0 0% 100%)",
      border: "hsl(0 0% 88%)",
      input: "hsl(0 0% 88%)",
      ring: "hsl(206 100% 41%)",
      success: "hsl(120 78% 27%)",
      successForeground: "hsl(0 0% 100%)",
      warning: "hsl(36 100% 31%)",
      warningForeground: "hsl(0 0% 100%)",
      // Fluent light puts the page at 95% grey, a darker backdrop than the
      // white card — so the ramp is measured against the page, not the card.
      "cat-1": "hsl(215 80% 44%)",
      "cat-2": "hsl(27 80% 34%)",
      "cat-3": "hsl(142 80% 25%)",
      "cat-4": "hsl(264 80% 56%)",
      "cat-5": "hsl(358 80% 43%)",
      "cat-6": "hsl(182 80% 25%)",
      "cat-7": "hsl(324 80% 41%)",
      "cat-8": "hsl(66 80% 23%)",
      "cat-9": "hsl(104 80% 24%)",
      "cat-10": "hsl(240 80% 54%)",
      "cat-11": "hsl(292 80% 40%)",
      "cat-12": "hsl(46 80% 27%)",
      sidebarBackground: "hsl(0 0% 98%)",
      sidebarAccent: "hsl(0 0% 92%)",
      sidebarBorder: "hsl(0 0% 88%)",
    },
  },
};

/**
 * Material — Google's Material 3. Generously rounded, Roboto, flat surfaces
 * with elevation expressed through shadow rather than blur or glow. Colours
 * follow the M3 baseline scheme (source colour #6750A4).
 */
const material: Theme = {
  id: "material",
  name: "Material",
  description: "Google's Material 3 — rounded shapes, Roboto, flat surfaces with elevation shadows.",
  inspiration: "Google Material Design 3 baseline scheme",
  iconPack: "material",
  typography: {
    app: `${ROBOTO}, Roboto, ${SYSTEM_FALLBACK}`,
    mono: `${ROBOTO_MONO}, "Roboto Mono", ${MONO_FALLBACK}`,
    headingLetterSpacing: "0em",
    headingWeight: "500",
    labelWeight: "500",
  },
  shape: { radius: "1rem", borderWidth: "1px" },
  effects: {
    // Material is flat: no blur, no glow, depth comes entirely from elevation.
    glassBlur: "0px",
    glassOpacity: "1",
    glassOpacityStrong: "1",
    glowStrength: "0",
    shadow: "0 1px 2px hsl(0 0% 0% / 0.3), 0 2px 6px 2px hsl(0 0% 0% / 0.15)",
    motionDuration: "0.2s",
    motionEasing: "cubic-bezier(0.2, 0, 0, 1)",
  },
  controls: {
    // M3 buttons are full pills, and hover is an 8% overlay of the foreground
    // colour rather than a change in opacity.
    buttonRadius: "9999px",
    filledBorderWidth: "0px",
    stateLayerOpacity: "0.08",
    focusRingWidth: "3px",
    labelTracking: "0.00714em",
    labelTransform: "none",
  },
  surfaces: {
    monaco: { dark: "vs-dark", light: "vs" },
    shiki: { dark: "material-theme-darker", light: "material-theme-lighter" },
  },
  colors: {
    dark: {
      background: "hsl(270 12% 9%)",
      foreground: "hsl(274 21% 90%)",
      card: "hsl(266 11% 14%)",
      cardForeground: "hsl(274 21% 90%)",
      popover: "hsl(264 11% 18%)",
      popoverForeground: "hsl(274 21% 90%)",
      primary: "hsl(258 100% 87%)",
      primaryForeground: "hsl(261 58% 28%)",
      secondary: "hsl(263 8% 22%)",
      secondaryForeground: "hsl(274 21% 90%)",
      muted: "hsl(270 8% 12%)",
      mutedForeground: "hsl(266 16% 80%)",
      accent: "hsl(340 60% 83%)",
      accentForeground: "hsl(338 33% 22%)",
      destructive: "hsl(2 65% 83%)",
      destructiveForeground: "hsl(4 70% 22%)",
      border: "hsl(266 8% 29%)",
      input: "hsl(266 8% 29%)",
      ring: "hsl(258 100% 87%)",
      success: "hsl(140 45% 75%)",
      successForeground: "hsl(142 60% 14%)",
      warning: "hsl(35 90% 78%)",
      warningForeground: "hsl(30 70% 16%)",
      // Softer than RapidTool's: M3 expresses categories as tonal roles, not
      // as fully saturated hues.
      "cat-1": "hsl(215 58% 62%)",
      "cat-2": "hsl(27 58% 54%)",
      "cat-3": "hsl(142 58% 42%)",
      "cat-4": "hsl(264 58% 69%)",
      "cat-5": "hsl(358 58% 66%)",
      "cat-6": "hsl(182 58% 42%)",
      "cat-7": "hsl(324 58% 65%)",
      "cat-8": "hsl(66 58% 39%)",
      "cat-9": "hsl(104 58% 40%)",
      "cat-10": "hsl(240 58% 70%)",
      "cat-11": "hsl(292 58% 66%)",
      "cat-12": "hsl(46 58% 44%)",
      sidebarBackground: "hsl(270 8% 12%)",
      sidebarAccent: "hsl(263 8% 22%)",
      sidebarBorder: "hsl(266 8% 24%)",
    },
    light: {
      background: "hsl(293 100% 98%)",
      foreground: "hsl(270 8% 12%)",
      card: "hsl(0 0% 100%)",
      cardForeground: "hsl(270 8% 12%)",
      popover: "hsl(275 33% 95%)",
      popoverForeground: "hsl(270 8% 12%)",
      primary: "hsl(258 35% 48%)",
      primaryForeground: "hsl(0 0% 100%)",
      secondary: "hsl(270 27% 92%)",
      secondaryForeground: "hsl(270 8% 12%)",
      muted: "hsl(274 21% 94%)",
      mutedForeground: "hsl(266 8% 29%)",
      accent: "hsl(339 21% 41%)",
      accentForeground: "hsl(0 0% 100%)",
      destructive: "hsl(3 71% 41%)",
      destructiveForeground: "hsl(0 0% 100%)",
      border: "hsl(266 16% 80%)",
      input: "hsl(266 16% 80%)",
      ring: "hsl(258 35% 48%)",
      success: "hsl(139 68% 25%)",
      successForeground: "hsl(0 0% 100%)",
      warning: "hsl(43 100% 24%)",
      warningForeground: "hsl(0 0% 100%)",
      "cat-1": "hsl(215 58% 45%)",
      "cat-2": "hsl(27 58% 37%)",
      "cat-3": "hsl(142 58% 29%)",
      "cat-4": "hsl(264 58% 55%)",
      "cat-5": "hsl(358 58% 47%)",
      "cat-6": "hsl(182 58% 29%)",
      "cat-7": "hsl(324 58% 45%)",
      "cat-8": "hsl(66 58% 27%)",
      "cat-9": "hsl(104 58% 28%)",
      "cat-10": "hsl(240 58% 52%)",
      "cat-11": "hsl(292 58% 42%)",
      "cat-12": "hsl(46 58% 31%)",
      sidebarBackground: "hsl(280 43% 96%)",
      sidebarAccent: "hsl(270 27% 92%)",
      sidebarBorder: "hsl(266 16% 84%)",
    },
  },
};

/**
 * Graphite — a quiet, near-monochrome workspace. Sharp corners, monospace
 * headings, effects dialled off. Useful as a low-distraction mode and as
 * proof that the font and effect axes are real and not colour-only.
 */
const graphite: Theme = {
  id: "graphite",
  name: "Graphite",
  description: "Low-distraction monochrome — sharp corners, monospace headings, no glass or glow.",
  iconPack: "lucide",
  typography: {
    app: `${GEIST}, ${SYSTEM_FALLBACK}`,
    mono: `${GEIST_MONO}, ${MONO_FALLBACK}`,
    display: `${GEIST_MONO}, ${MONO_FALLBACK}`,
    headingLetterSpacing: "-0.02em",
    headingWeight: "600",
    labelWeight: "500",
  },
  shape: { radius: "0.125rem", borderWidth: "1px" },
  effects: {
    glassBlur: "8px",
    glassOpacity: "0.94",
    glassOpacityStrong: "0.98",
    glowStrength: "0",
    shadow: "0 1px 2px hsl(0 0% 0% / 0.2)",
    motionDuration: "0.12s",
    motionEasing: "ease-out",
  },
  controls: {
    // Upper-case labels and a hairline focus ring — the industrial register
    // this theme is going for, and proof the axis is real.
    buttonRadius: "var(--radius)",
    filledBorderWidth: "1px",
    stateLayerOpacity: "0",
    focusRingWidth: "1px",
    labelTracking: "0.05em",
    labelTransform: "uppercase",
  },
  surfaces: {
    monaco: { dark: "hc-black", light: "hc-light" },
    shiki: { dark: "min-dark", light: "min-light" },
  },
  colors: {
    dark: {
      background: "hsl(0 0% 7%)",
      foreground: "hsl(0 0% 93%)",
      card: "hsl(0 0% 10%)",
      cardForeground: "hsl(0 0% 93%)",
      popover: "hsl(0 0% 12%)",
      popoverForeground: "hsl(0 0% 93%)",
      primary: "hsl(0 0% 88%)",
      primaryForeground: "hsl(0 0% 8%)",
      secondary: "hsl(0 0% 15%)",
      secondaryForeground: "hsl(0 0% 93%)",
      muted: "hsl(0 0% 14%)",
      mutedForeground: "hsl(0 0% 60%)",
      accent: "hsl(35 90% 60%)",
      accentForeground: "hsl(0 0% 8%)",
      destructive: "hsl(0 70% 65%)",
      destructiveForeground: "hsl(0 0% 8%)",
      border: "hsl(0 0% 18%)",
      input: "hsl(0 0% 18%)",
      ring: "hsl(0 0% 70%)",
      success: "hsl(140 55% 55%)",
      successForeground: "hsl(0 0% 8%)",
      warning: "hsl(45 90% 58%)",
      warningForeground: "hsl(0 0% 8%)",
      // The quietest ramp we ship: Graphite is near-monochrome, so categories
      // are told apart by hue at LOW saturation rather than by vividness.
      // Below ~40% they stop being separable at chip size, which is the floor
      // this sits just above.
      "cat-1": "hsl(215 45% 59%)",
      "cat-2": "hsl(27 45% 52%)",
      "cat-3": "hsl(142 45% 43%)",
      "cat-4": "hsl(264 45% 65%)",
      "cat-5": "hsl(358 45% 63%)",
      "cat-6": "hsl(182 45% 43%)",
      "cat-7": "hsl(324 45% 61%)",
      "cat-8": "hsl(66 45% 40%)",
      "cat-9": "hsl(104 45% 41%)",
      "cat-10": "hsl(240 45% 66%)",
      "cat-11": "hsl(292 45% 62%)",
      "cat-12": "hsl(46 45% 44%)",
      sidebarBackground: "hsl(0 0% 9%)",
      sidebarAccent: "hsl(0 0% 14%)",
      sidebarBorder: "hsl(0 0% 17%)",
    },
    light: {
      background: "hsl(0 0% 99%)",
      foreground: "hsl(0 0% 10%)",
      card: "hsl(0 0% 100%)",
      cardForeground: "hsl(0 0% 10%)",
      popover: "hsl(0 0% 100%)",
      popoverForeground: "hsl(0 0% 10%)",
      primary: "hsl(0 0% 16%)",
      primaryForeground: "hsl(0 0% 100%)",
      secondary: "hsl(0 0% 95%)",
      secondaryForeground: "hsl(0 0% 10%)",
      muted: "hsl(0 0% 96%)",
      mutedForeground: "hsl(0 0% 42%)",
      accent: "hsl(30 90% 37%)",
      accentForeground: "hsl(0 0% 100%)",
      destructive: "hsl(0 70% 42%)",
      destructiveForeground: "hsl(0 0% 100%)",
      border: "hsl(0 0% 89%)",
      input: "hsl(0 0% 89%)",
      ring: "hsl(0 0% 40%)",
      success: "hsl(140 65% 26%)",
      successForeground: "hsl(0 0% 100%)",
      warning: "hsl(35 95% 32%)",
      warningForeground: "hsl(0 0% 100%)",
      "cat-1": "hsl(215 45% 45%)",
      "cat-2": "hsl(27 45% 39%)",
      "cat-3": "hsl(142 45% 32%)",
      "cat-4": "hsl(264 45% 54%)",
      "cat-5": "hsl(358 45% 49%)",
      "cat-6": "hsl(182 45% 32%)",
      "cat-7": "hsl(324 45% 47%)",
      "cat-8": "hsl(66 45% 30%)",
      "cat-9": "hsl(104 45% 31%)",
      "cat-10": "hsl(240 45% 52%)",
      "cat-11": "hsl(292 45% 44%)",
      "cat-12": "hsl(46 45% 33%)",
      sidebarBackground: "hsl(0 0% 97%)",
      sidebarAccent: "hsl(0 0% 94%)",
      sidebarBorder: "hsl(0 0% 89%)",
    },
  },
};

/** Every built-in theme, in the order Settings presents them. */
export const THEMES: Theme[] = [rapidtool, fluent, material, graphite];

/** The theme used when nothing has been chosen and no org default is set. */
export const DEFAULT_THEME_ID = rapidtool.id;

const THEMES_BY_ID = new Map(THEMES.map((t) => [t.id, t]));

/** Look up a theme by id, or `undefined` when the id is unknown. */
export function findTheme(id: string | null | undefined): Theme | undefined {
  return id ? THEMES_BY_ID.get(id) : undefined;
}

/**
 * Resolve an id to a real theme, falling back to the default. Used on every
 * boundary where an id arrives from outside the app (stored preference, API
 * response, URL) and may name a theme that has since been removed.
 */
export function resolveTheme(id: string | null | undefined): Theme {
  return findTheme(id) ?? THEMES_BY_ID.get(DEFAULT_THEME_ID)!;
}
