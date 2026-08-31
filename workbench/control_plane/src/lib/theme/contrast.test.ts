/**
 * Contrast gate — every theme, every mode.
 *
 * Themes are colours picked by eye, and the failure they invite is a pair that
 * reads fine to the author and not at all to someone on a dimmer screen. This
 * runs the arithmetic so a new theme cannot ship unreadable.
 *
 * The RapidTool exceptions
 * ------------------------
 * Seven pairs in the default theme are below AA. They are the app's ORIGINAL
 * colours, preserved token-for-token when the theming engine was built, and
 * fixing them means changing how the shipped product looks — a design decision
 * for whoever owns the brand, not a side effect of adding a test.
 *
 * So they are recorded below with their measured ratios and treated as a
 * ratchet: each may not get worse, and `every recorded shortfall is still a
 * shortfall` fails once one is fixed, which forces the entry to be deleted
 * rather than quietly outliving the problem. New themes get no such latitude.
 */

import { describe, expect, it } from "vitest";

import { AA_LARGE_TEXT, AA_NORMAL_TEXT, accentInk, contrast, parseColor } from "./contrast";
import { THEME } from "./themes";
import { CATEGORICAL_TOKENS } from "./types";
import type { ColorTokens } from "./types";

type Pair = {
  fg: keyof ColorTokens;
  bg: keyof ColorTokens;
  min: number;
};

/**
 * Token pairs that actually carry text or convey state.
 *
 * `min` is AA for body text (4.5) where the pair renders words, and the
 * large-text/UI threshold (3.0) where it renders a status dot, an icon or a
 * chart mark against a surface.
 */
const PAIRS: Pair[] = [
  { fg: "foreground", bg: "background", min: AA_NORMAL_TEXT },
  { fg: "cardForeground", bg: "card", min: AA_NORMAL_TEXT },
  { fg: "popoverForeground", bg: "popover", min: AA_NORMAL_TEXT },
  { fg: "mutedForeground", bg: "background", min: AA_NORMAL_TEXT },
  { fg: "mutedForeground", bg: "card", min: AA_NORMAL_TEXT },
  { fg: "primaryForeground", bg: "primary", min: AA_NORMAL_TEXT },
  { fg: "secondaryForeground", bg: "secondary", min: AA_NORMAL_TEXT },
  { fg: "accentForeground", bg: "accent", min: AA_NORMAL_TEXT },
  { fg: "destructiveForeground", bg: "destructive", min: AA_NORMAL_TEXT },
  { fg: "successForeground", bg: "success", min: AA_NORMAL_TEXT },
  { fg: "warningForeground", bg: "warning", min: AA_NORMAL_TEXT },
  // Status colours used as text/dots directly on a surface.
  { fg: "primary", bg: "background", min: AA_LARGE_TEXT },
  { fg: "primary", bg: "card", min: AA_LARGE_TEXT },
  { fg: "destructive", bg: "card", min: AA_LARGE_TEXT },
  { fg: "success", bg: "card", min: AA_LARGE_TEXT },
  { fg: "warning", bg: "card", min: AA_LARGE_TEXT },
  // The categorical ramp, at the BODY-TEXT threshold rather than the 3.0 used
  // for dots and icons: a `--cat-*` slot's main job is `text-cat-3` on a
  // context chip, i.e. small words, and a chip is measured against both the
  // card it sits on and the page behind it (Fluent's light page is 95% grey,
  // a full stop darker than its white card, so the two are not the same test).
  ...CATEGORICAL_TOKENS.flatMap((token) => [
    { fg: token, bg: "card", min: AA_NORMAL_TEXT } as Pair,
    { fg: token, bg: "background", min: AA_NORMAL_TEXT } as Pair,
  ]),
];

const key = (themeId: string, mode: string, p: Pair) => `${themeId}/${mode}/${p.fg}-on-${p.bg}`;

/**
 * Pre-existing shortfalls in the original design, with the ratio measured when
 * the gate was added. Values may improve; they may not regress.
 *
 * Every entry here is a real readability problem for someone. Removing one
 * means picking a new colour — see the module comment.
 */
const KNOWN_SHORTFALLS: Record<string, number> = {
  "rapidtool/dark/destructiveForeground-on-destructive": 3.63,
  "rapidtool/light/accentForeground-on-accent": 2.16,
  "rapidtool/light/destructiveForeground-on-destructive": 3.59,
  "rapidtool/light/successForeground-on-success": 1.9,
  "rapidtool/light/warningForeground-on-warning": 1.5,
  "rapidtool/light/success-on-card": 1.99,
  "rapidtool/light/warning-on-card": 1.57,
};

/** Float noise guard — ratios are compared to two decimal places. */
const EPSILON = 0.01;

type Measured = { id: string; ratio: number; min: number };

const measurements: Measured[] = [];
for (const mode of ["dark", "light"] as const) {
  for (const pair of PAIRS) {
    const fg = THEME.colors[mode][pair.fg];
    const bg = THEME.colors[mode][pair.bg];
    if (!fg || !bg) continue;
    const ratio = contrast(fg, bg);
    if (ratio === null) continue;
    measurements.push({ id: key(THEME.id, mode, pair), ratio, min: pair.min });
  }
}

describe("contrast maths", () => {
  it("computes the reference ratios correctly", () => {
    // Black on white is the WCAG maximum; a colour against itself is 1.
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    // hsl and hex forms of the same colour must agree.
    expect(contrast("hsl(0 0% 0%)", "#ffffff")).toBeCloseTo(21, 1);
  });

  it("returns null rather than a wrong number for colours it cannot parse", () => {
    // A silent 0 or 21 here would turn an unchecked colour into a pass.
    expect(parseColor("var(--primary)")).toBeNull();
    expect(contrast("color-mix(in srgb, red, blue)", "#fff")).toBeNull();
  });

  it("parses every colour form the manifest uses", () => {
    for (const mode of ["dark", "light"] as const) {
      for (const [token, value] of Object.entries(THEME.colors[mode])) {
        if (!value) continue;
        expect(parseColor(value as string), `${mode}/${token}`).not.toBeNull();
      }
    }
  });
});

describe("theme contrast", () => {
  it("checks a meaningful number of pairs", () => {
    // Guards against a refactor that silently stops measuring anything.
    // Was >100 across four themes; one theme in two modes measures ~80, so
    // the floor moved with the divisor rather than being quietly deleted.
    expect(measurements.length).toBeGreaterThan(60);
  });

  it.each(measurements.map((m) => [m.id, m] as const))(
    "%s meets WCAG AA",
    (_id, m) => {
      const allowance = KNOWN_SHORTFALLS[m.id];
      if (allowance !== undefined) {
        // Ratchet: a recorded shortfall may improve, never regress.
        expect(
          m.ratio,
          `${m.id} regressed below its recorded ${allowance}:1`,
        ).toBeGreaterThanOrEqual(allowance - EPSILON);
        return;
      }
      expect(
        m.ratio,
        `${m.id} is ${m.ratio.toFixed(2)}:1, below the required ${m.min}:1`,
      ).toBeGreaterThanOrEqual(m.min);
    },
  );

  it("every recorded shortfall is still a shortfall", () => {
    // Keeps the exception list honest: fix a colour and this fails until the
    // entry is deleted, so the list can never grant latitude nobody needs.
    const byId = new Map(measurements.map((m) => [m.id, m]));
    const nowPassing = Object.keys(KNOWN_SHORTFALLS).filter((id) => {
      const m = byId.get(id);
      return m && m.ratio >= m.min;
    });
    expect(
      nowPassing,
      "these now pass — delete them from KNOWN_SHORTFALLS",
    ).toEqual([]);
  });

  it("every recorded shortfall names a pair that is still measured", () => {
    // A renamed token would otherwise leave a dead entry excusing nothing.
    const ids = new Set(measurements.map((m) => m.id));
    const stale = Object.keys(KNOWN_SHORTFALLS).filter((id) => !ids.has(id));
    expect(stale, "these no longer correspond to a measured pair").toEqual([]);
  });

  it("confines the exception list to the original theme", () => {
    // Themes added after the gate went in have no excuse for shipping below AA.
    const offenders = Object.keys(KNOWN_SHORTFALLS).filter(
      (id) => !id.startsWith("rapidtool/"),
    );
    expect(offenders, "new themes must meet AA outright").toEqual([]);
  });
});

describe("accentInk", () => {
  /**
   * The bug: an accent override replaced `--primary` but not the ink drawn on
   * it, so Graphite dark (primary near-white, therefore primary-foreground
   * near-black) rendered black-on-red for every primary-filled surface — the
   * sidebar logo mark, every primary button, every active pill.
   */
  it("picks the ink that is actually legible on the fill", () => {
    expect(accentInk("#ffffff")).toBe("#000000");
    expect(accentInk("#000000")).toBe("#ffffff");
    // The five presets offered in Settings → Appearance.
    for (const preset of [
      "hsl(206 100% 42%)", // Azure
      "hsl(258 60% 55%)",  // Violet
      "hsl(158 64% 38%)",  // Emerald
      "hsl(32 95% 48%)",   // Amber
      "hsl(347 77% 50%)",  // Rose
    ]) {
      const ink = accentInk(preset);
      expect(ink, preset).not.toBe("");
      // The claim is not "an ink was chosen" but "this ink is readable".
      expect(contrast(ink, preset)!, `${ink} on ${preset}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("beats the alternative ink on every preset", () => {
    // A weaker test would pass by always answering black. This asserts the
    // CHOICE — that the other option would have been worse.
    for (const preset of ["hsl(206 100% 42%)", "hsl(32 95% 48%)", "hsl(347 77% 50%)"]) {
      const chosen = accentInk(preset);
      const other = chosen === "#000000" ? "#ffffff" : "#000000";
      expect(contrast(chosen, preset)!, preset).toBeGreaterThan(contrast(other, preset)!);
    }
  });

  it("returns empty for a colour it cannot parse, keeping the theme's pairing", () => {
    // Guessing would be worse than deferring: the theme's own primary/ink pair
    // is at least a pair somebody chose.
    expect(accentInk("rebeccapurple")).toBe("");
    expect(accentInk("not-a-colour")).toBe("");
  });
});
