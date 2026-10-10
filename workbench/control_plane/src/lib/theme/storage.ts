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
 * The appearance scope of the account that loaded LAST (`<email>|<orgId>`).
 * It names an account and holds no preference. The boot script reads it,
 * and the first bind of a tab reads it. No other read uses it, because one
 * pointer serves every tab, and two tabs can show two accounts for a moment.
 */
export const APPEARANCE_SCOPE_KEY = "cc-appearance-scope";

/**
 * The prefix of the last full scope seen for one email. An account switch
 * knows the email of the target and not its organization, so it reads this.
 */
export const APPEARANCE_LAST_SCOPE_PREFIX = "cc-appearance-last:";

/**
 * Set once the bare keys of a browser from before the scopes have moved into
 * the first scope. A sign-out removes the pointer, and this marker stops a
 * second adoption that would give the next account the bare `theme`.
 */
export const APPEARANCE_ADOPTED_KEY = "cc-appearance-adopted";

/** Joins a key and its scope. The boot script takes it from here. */
export const SCOPE_SEPARATOR = ":";

/** Every key that has a copy for each scope. */
export const SCOPED_KEYS = [...Object.values(STORAGE_KEYS), MODE_STORAGE_KEY] as const;

/** The key of `key` in `scope`, or the bare key when there is no scope. */
export function scopedKey(key: string, scope: string | null): string {
  return scope ? `${key}${SCOPE_SEPARATOR}${scope}` : key;
}

/** The scope the pointer names now, or null. */
export function activeAppearanceScope(): string | null {
  return read(APPEARANCE_SCOPE_KEY) || null;
}

// ---------------------------------------------------------------------------
// The scope of THIS tab (review round 2, P1)
// ---------------------------------------------------------------------------
// Two tabs on two accounts share one pointer. When each read and write
// followed the pointer, the tabs moved it back and forth and swapped the two
// accounts' modes. So each tab keeps its own scope in module state. It starts
// as the pointer at the first read, which is the scope the boot script
// painted, and only this tab's bind changes it. Every read and write of
// `themeStorage` goes to it.
//
// A tab that sees ANOTHER tab move the pointer to a different scope is stale.
// The cookie now names that other account, and the tab-sync reload is coming
// (`lib/accountSwitch.ts`). It goes passive until it reloads: no bind, no
// mirror, no write.

const tab = {
  known: false,
  scope: null as string | null,
  passive: false,
  /** A mode next-themes took from ANOTHER tab, not chosen in this one. */
  externalMode: null as string | null,
};

/** The scope this tab reads and writes, or null for the bare keys. */
export function tabAppearanceScope(): string | null {
  if (!tab.known) {
    tab.known = true;
    tab.scope = activeAppearanceScope();
  }
  return tab.scope;
}

/** For `scope.ts`: this tab now shows `scope`. */
export function setTabAppearanceScope(scope: string | null): void {
  tab.known = true;
  tab.scope = scope;
}

/** True once another tab moved the pointer away from this tab's scope. */
export function isAppearancePassive(): boolean {
  return tab.passive;
}

/**
 * For `scope.ts`: this tab is not stale after all. A tab that loaded with no
 * pointer goes passive when a sibling tab of the SAME account binds first.
 * The pointer then names this tab's own account, and its bind resumes it.
 */
export function resumeAppearance(): void {
  tab.passive = false;
}

/**
 * The `storage` event handler. `ThemeProvider` installs it with
 * `watchAppearanceStorage`. Exported for the tests.
 */
export function onAppearanceStorage(e: { key: string | null; newValue: string | null }): void {
  if (e.key === APPEARANCE_SCOPE_KEY) {
    if (e.newValue && e.newValue !== tabAppearanceScope()) tab.passive = true;
  } else if (e.key === MODE_STORAGE_KEY) {
    // next-themes applies a removed key as its default mode, so this does too.
    tab.externalMode = e.newValue || DEFAULT_MODE;
  }
}

/** Listen for the other tabs. Returns the cleanup. */
export function watchAppearanceStorage(): () => void {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") return () => {};
  const handler = (e: StorageEvent) => onAppearanceStorage(e);
  window.addEventListener("storage", handler);
  return () => window.removeEventListener("storage", handler);
}

/** Tests only: start a new page. Storage stays, as on a reload. */
export function __newAppearanceTabForTests(): void {
  tab.known = false;
  tab.scope = null;
  tab.passive = false;
  tab.externalMode = null;
}

/** The key of `key` in this tab's scope. */
const inTab = (key: string) => scopedKey(key, tabAppearanceScope());

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

/** A write of this tab's preferences. A passive tab writes nothing. */
function tabWrite(key: string, value: string | null): void {
  if (!tab.passive) write(inTab(key), value);
}

/**
 * Every read and write goes to THIS TAB's scope (see above), never to the
 * pointer that the other tabs also move.
 */
export const themeStorage = {
  getDensity: () => read(inTab(STORAGE_KEYS.density)) as Density | null,
  setDensity: (d: Density | null) => tabWrite(STORAGE_KEYS.density, d),
  getAccent: () => read(inTab(STORAGE_KEYS.accent)),
  getAccentInk: () => read(inTab(STORAGE_KEYS.accentInk)),
  setAccent: (c: string | null) => {
    tabWrite(STORAGE_KEYS.accent, c);
    // Written together so the two can never disagree, and cleared together so
    // removing the accent cannot leave orphaned ink behind.
    tabWrite(STORAGE_KEYS.accentInk, c ? accentInk(c) || null : null);
  },
  getOrgDensity: () => read(inTab(STORAGE_KEYS.orgDensity)) as Density | null,
  setOrgDensity: (d: Density) => tabWrite(STORAGE_KEYS.orgDensity, d),
  /** The colour mode of this tab's scope, or the bare key with no scope. */
  getMode: () => read(inTab(MODE_STORAGE_KEY)) as ThemeMode | null,
  /**
   * Record a colour mode chosen IN this tab, for this tab's scope. It skips a
   * mode that next-themes took from another tab's `storage` event, a passive
   * tab, and a tab with no scope (next-themes already wrote the bare key).
   */
  mirrorMode: (mode: string) => {
    const external = tab.externalMode;
    tab.externalMode = null;
    if (external !== null && external === mode) return;
    const scope = tabAppearanceScope();
    if (scope && !tab.passive) write(scopedKey(MODE_STORAGE_KEY, scope), mode);
  },
};

/** Low-level access for `scope.ts`, which moves the pointer. */
export const rawStorage = { read, write };

/** Root font-size multiplier for a density setting. */
export function densityScale(density: Density | null | undefined): number {
  return density ? DENSITY_SCALE[density] : DENSITY_SCALE.default;
}
