/**
 * The chart theme — the Metorite chart language (WS-47 WAC-10f).
 *
 * ONE look for every chart the product draws: the web chat's live charts and
 * the WhatsApp images, which this same code draws on the server. It is plain
 * JavaScript, not TypeScript, because the gateway runs `render.mjs` with the
 * box's Node 20, which cannot strip types.
 *
 * ⚠️ NOT a second palette. Every value here is a token of `lib/theme/themes.ts`
 * (which `themes.test.ts` holds to `globals.css`), or one of its hues lifted
 * to read on a chart. `theme.test.ts` fails if a value drifts from the token.
 * Change the token, then this file, or the test stops you
 * (`charts.test.ts`, `wac10f-one-palette`).
 */

/** A `hsl(h s% l%)` triple as `#rrggbb`. Every renderer reads hex. */
export function hsl(h, s, l) {
  const S = s / 100, L = l / 100;
  const a = S * Math.min(L, 1 - L);
  const f = (/** @type {number} */ n) => {
    const k = (n + h / 30) % 12;
    return Math.round(255 * (L - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  };
  return "#" + [f(0), f(8), f(4)].map((v) => v.toString(16).padStart(2, "0")).join("");
}

/** A hex colour at opacity *a*, as `rgba()`. resvg and ECharts both read it,
 * and neither reads the CSS4 `hsl(h s l / a)` form. */
export function alpha(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

/** The categorical slots a chart uses, in the order a chart uses them. Each
 * is a `--cat-n` hue: neighbours differ most (blue, green, orange, violet). */
export const SERIES_SLOTS = [1, 3, 2, 4, 5, 6, 12, 7];
/** The `--cat-n` hues (themes.ts). */
export const CAT_HUES = { 1: 215, 2: 27, 3: 142, 4: 264, 5: 358, 6: 182, 7: 324, 12: 46 };
/** The ramp's lightness on a chart: lifted in dark mode so a thin line still
 * reads on the card, and the light mode's own depth on white. */
const SERIES_L = { dark: 64, light: 46 };
const SERIES_S = 85;

/**
 * @typedef {"dark" | "light"} Mode
 * @typedef {{ bg: string, card: string, fg: string, muted: string, faint: string,
 *   grid: string, series: string[], status: Record<string, string>, font: string }} ChartTheme
 */

/** @type {Record<Mode, Omit<ChartTheme, "series" | "font">>} */
const BASE = {
  dark: {
    bg: hsl(220, 13, 8),            // --background
    card: hsl(220, 13, 10),         // --card
    fg: hsl(210, 40, 98),           // --foreground
    muted: hsl(215, 20, 65),        // --muted-foreground
    faint: hsl(215, 14, 42),        // muted, one step down: the footer
    grid: hsl(220, 13, 17),         // --border, one step up: reads on --card
    // statusAccent.ts's six hues, as the dark tokens draw them
    status: {
      green: hsl(142, 76, 47), amber: hsl(47, 96, 53), red: hsl(0, 72, 66),
      blue: hsl(198, 89, 55), violet: hsl(268, 80, 70), gray: hsl(215, 20, 65),
    },
  },
  light: {
    bg: hsl(0, 0, 100),
    card: hsl(0, 0, 100),
    fg: hsl(222.2, 84, 4.9),
    muted: hsl(215.4, 16.3, 46.9),
    faint: hsl(215, 16, 62),
    grid: hsl(214.3, 31.8, 91.4),
    status: {
      green: hsl(142, 72, 28), amber: hsl(36, 92, 29), red: hsl(0, 72, 45),
      blue: hsl(198, 89, 32), violet: hsl(268, 70, 50), gray: hsl(215.4, 16.3, 46.9),
    },
  },
};

/** @param {Mode} mode @returns {ChartTheme} */
export function theme(mode = "dark") {
  const m = mode === "light" ? "light" : "dark";
  return {
    ...BASE[m],
    series: SERIES_SLOTS.map((slot) => hsl(CAT_HUES[/** @type {keyof typeof CAT_HUES} */ (slot)], SERIES_S, SERIES_L[m])),
    font: "Geist",
  };
}

/**
 * The six hues of `statusAccent.ts` by name, and nothing more.
 *
 * ⚠️ NOT a status vocabulary. A status WORD ("On hold", "Shipped") is turned
 * into one of these six names BEFORE it reaches the chart, by the one seam
 * that owns that rule: `statusAccent.ts` in the web app, and its fenced
 * Python mirror `whatsapp_cards.hue` on the WhatsApp side. A second list here
 * drew "On hold" blue where the product draws it amber (review, 2026-10-11).
 */
const HUE_NAMES = new Set(["green", "amber", "red", "blue", "violet", "gray"]);

/** The colour a hue name names, or null. @param {ChartTheme} t */
export function tone(t, name) {
  if (typeof name !== "string") return null;
  const key = name.trim().toLowerCase();
  return HUE_NAMES.has(key) ? t.status[key] : null;
}
