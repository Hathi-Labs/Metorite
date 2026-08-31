/**
 * Where appearance preferences live in the browser.
 *
 * Two readers share these keys: the React store, and the pre-paint boot script
 * (`boot.ts`) which runs before hydration and cannot import anything. The key
 * names are therefore defined once here and interpolated into the script
 * source, so the two can never drift apart.
 */

import type { Density, ThemeMode } from "./types";
import { DENSITY_SCALE } from "./types";

import { accentInk } from "./contrast";

export const STORAGE_KEYS = {
  /** The member's own density choice. Absent means "follow the org default". */
  density: "cc-density",
  /** Optional primary-colour override. */
  accent: "cc-accent",
  //: The ink that pairs with the stored accent, computed by `accentInk` at the
  //: moment the accent is SET. Stored rather than derived in the pre-paint
  //: script so there is exactly one implementation of the luminance decision —
  //: a second, minified copy inside a template string is precisely the kind of
  //: duplicate that drifts and that nobody can test.
  accentInk: "cc-accent-ink",
  /**
   * Last-known organisation defaults, cached so a member who has never opened
   * Settings still gets the company look on first paint instead of a flash of
   * the built-in default.
   */
  orgDensity: "cc-density-org",
} as const;

// ⚠️ `cc-theme`, `cc-theme-org` and the `data-theme` attribute were removed
// on 2026-08-31 with the theming engine. A browser that still holds those
// keys simply keeps two ignored strings — nothing reads them, and clearing
// them would need a migration for no gain.

/** Custom property the density setting drives. */
export const DENSITY_PROPERTY = "--ui-scale";

/** Custom properties an accent override replaces. */
export const ACCENT_PROPERTIES = ["--primary", "--ring", "--sidebar-primary"] as const;

/**
 * The INK tokens that must follow the accent, not the theme.
 *
 * Setting `--primary` without these leaves the theme's own primary-foreground
 * painted on a colour it was never chosen for — white ink on a pale accent.
 * Kept as a separate list because the value differs: these get
 * `accentInk(accent)`, the others get the accent itself.
 */
export const ACCENT_INK_PROPERTIES = ["--primary-foreground", "--sidebar-primary-foreground"] as const;

/** localStorage key next-themes uses for the colour mode. */
export const MODE_STORAGE_KEY = "theme";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    // Private mode / blocked storage: fall back to defaults rather than crash.
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Preferences simply do not persist when storage is unavailable.
  }
}

export const themeStorage = {
  getDensity: () => read(STORAGE_KEYS.density) as Density | null,
  setDensity: (d: Density | null) => write(STORAGE_KEYS.density, d),
  getAccent: () => read(STORAGE_KEYS.accent),
  getAccentInk: () => read(STORAGE_KEYS.accentInk),
  setAccent: (c: string | null) => {
    write(STORAGE_KEYS.accent, c);
    // Written together so the two can never disagree, and cleared together so
    // removing the accent cannot leave orphaned ink behind.
    write(STORAGE_KEYS.accentInk, c ? accentInk(c) || null : null);
  },
  getOrgDensity: () => read(STORAGE_KEYS.orgDensity) as Density | null,
  setOrgDensity: (d: Density) => write(STORAGE_KEYS.orgDensity, d),
  getMode: () => read(MODE_STORAGE_KEY) as ThemeMode | null,
};

/** Root font-size multiplier for a density setting. */
export function densityScale(density: Density | null | undefined): number {
  return density ? DENSITY_SCALE[density] : DENSITY_SCALE.default;
}
