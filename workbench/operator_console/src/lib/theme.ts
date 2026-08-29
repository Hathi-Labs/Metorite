// The theme seam — one storage key, one applier, read by two callers.
//
// ⚠️ **`layout.tsx` inlines a boot script that must agree with this file.** It
// cannot import from here (it runs before hydration, as a raw string in the
// document head), so the key is exported as a constant and the script is built
// from it rather than typed twice. A second literal `"…-theme"` string in the
// tree is the whole failure mode: the script would restore one key while the
// toggle wrote another, and the theme would silently stop persisting.

export type Theme = "light" | "dark";

export const STORAGE_KEY = "metorite-operator-theme";

/** The default when nothing is stored.
 *
 * ⚠️ **Dark, deliberately — this is not `prefers-color-scheme`.** The light
 * palette has never been looked at by a human, and `DESIGN_SYSTEM.md` §8 says
 * the real gate is switching the theme and LOOKING. Following the OS would hand
 * an unverified palette to every operator whose machine is set to light, which
 * is most of them. Once somebody has seen it, this becomes a media query. */
export const DEFAULT_THEME: Theme = "dark";

export function isTheme(v: unknown): v is Theme {
  return v === "light" || v === "dark";
}

/** What the operator last chose, or null if they never have.
 *
 * ⚠️ Wrapped, because `localStorage` THROWS in a browser set to block site
 * data rather than returning null. An uncaught throw in the top bar takes
 * sign-out and the elevation control down with it. */
export function readStoredTheme(): Theme | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return isTheme(raw) ? raw : null;
  } catch {
    return null;
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

/** The pre-hydration script, as a string for `dangerouslySetInnerHTML`.
 *
 * 🔴 This runs in the document head, before first paint. Without it the page
 * renders in the default theme and then snaps to the stored one, which on every
 * navigation reads as the app breaking rather than as a preference being
 * honoured.
 *
 * ⚠️ Self-contained and defensive on purpose: it cannot import, and anything it
 * throws happens before React exists, so it would leave a blank document. */
export function bootScript(): string {
  return (
    "(function(){try{var t=localStorage.getItem(" +
    JSON.stringify(STORAGE_KEY) +
    ");if(t===\"light\"||t===\"dark\"){document.documentElement.setAttribute(\"data-theme\",t)}}catch(e){}})()"
  );
}
