/**
 * Where appearance preferences live in the browser.
 *
 * Two readers share these keys: the React store, and the pre-paint boot script
 * (`boot.ts`) which runs before hydration and cannot import anything. The key
 * names are therefore defined once here and interpolated into the script
 * source, so the two can never drift apart.
 *
 * ⚠️ **Appearance is per ACCOUNT on this device** (owner bug, 2026-10-11).
 * The keys were fixed names, so every account signed in to one browser shared
 * one accent, one density and one colour mode. Now each value lives under
 * `<key>:<scope>`, and the scope is `<email>|<organizationId>`, the format of
 * `chatScope` in `lib/sessions.ts`. `APPEARANCE_SCOPE_KEY` names the active
 * scope. The boot script reads it before paint, because the session cookie
 * is httpOnly. With no pointer, every read and write uses the bare key, as
 * before. `scope.ts` moves the pointer. Fence: `scope.test.ts`.
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

/**
 * localStorage key next-themes uses for the colour mode. next-themes reads
 * this bare key and no other, so it stays. The record of an account's mode is
 * `theme:<scope>`. The boot script copies it into `theme` before next-themes
 * reads it, and `ThemeProvider` copies each change back.
 */
export const MODE_STORAGE_KEY = "theme";

/** The colour mode of an account that never chose one. `Providers.tsx` reads it. */
export const DEFAULT_MODE: ThemeMode = "dark";

/**
 * The active appearance scope (`<email>|<organizationId>`). It names an
 * account and holds no preference. Absent means this browser has never bound
 * one, so the bare keys still hold the values (see `scope.ts`, legacy).
 */
export const APPEARANCE_SCOPE_KEY = "cc-appearance-scope";

/**
 * The prefix of the last full scope seen for one email. An account switch
 * knows the email of the target and not its organization, so it reads this.
 */
export const APPEARANCE_LAST_SCOPE_PREFIX = "cc-appearance-last:";

/** Joins a key and its scope. The boot script takes it from here. */
export const SCOPE_SEPARATOR = ":";

/** The key of `key` in `scope`, or the bare key when there is no scope. */
export function scopedKey(key: string, scope: string | null): string {
  return scope ? `${key}${SCOPE_SEPARATOR}${scope}` : key;
}

/** The scope the pointer names now, or null. */
export function activeAppearanceScope(): string | null {
  return read(APPEARANCE_SCOPE_KEY) || null;
}

/** The key of `key` in the active scope. */
const here = (key: string) => scopedKey(key, activeAppearanceScope());

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

/**
 * Every read and write goes to the ACTIVE scope, resolved at the moment of
 * the call. So a pointer that moves takes the next read with it.
 */
export const themeStorage = {
  getDensity: () => read(here(STORAGE_KEYS.density)) as Density | null,
  setDensity: (d: Density | null) => write(here(STORAGE_KEYS.density), d),
  getAccent: () => read(here(STORAGE_KEYS.accent)),
  getAccentInk: () => read(here(STORAGE_KEYS.accentInk)),
  setAccent: (c: string | null) => {
    write(here(STORAGE_KEYS.accent), c);
    // Written together so the two can never disagree, and cleared together so
    // removing the accent cannot leave orphaned ink behind.
    write(here(STORAGE_KEYS.accentInk), c ? accentInk(c) || null : null);
  },
  getOrgDensity: () => read(here(STORAGE_KEYS.orgDensity)) as Density | null,
  setOrgDensity: (d: Density) => write(here(STORAGE_KEYS.orgDensity), d),
  /** The colour mode of the active scope, or the bare key with no scope. */
  getMode: () => read(here(MODE_STORAGE_KEY)) as ThemeMode | null,
  /**
   * Record a colour mode for the active scope. With no scope it does nothing,
   * because next-themes already wrote the bare key.
   */
  mirrorMode: (mode: string) => {
    const scope = activeAppearanceScope();
    if (scope) write(scopedKey(MODE_STORAGE_KEY, scope), mode);
  },
};

/** Low-level access for `scope.ts`, which moves the pointer. */
export const rawStorage = { read, write };

/** Root font-size multiplier for a density setting. */
export function densityScale(density: Density | null | undefined): number {
  return density ? DENSITY_SCALE[density] : DENSITY_SCALE.default;
}
