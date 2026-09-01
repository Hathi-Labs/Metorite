/**
 * Colour input validation.
 *
 * ⚠️ This file used to be the theming engine's compiler — it turned four
 * manifests into `html[data-theme="…"]` custom-property scopes, emitted once
 * into the document head, so switching themes was an attribute write. Owner
 * directive 2026-08-31 retired that engine. `globals.css` now carries the
 * one theme's tokens directly, so nothing generates a stylesheet any more
 * and `buildThemeCss` / `buildAllThemesCss` are gone with it.
 *
 * What survives is the part that was never about themes: the accent
 * override is the one appearance value that comes from USER INPUT and ends
 * up inside a CSS custom property, so it still needs a gate.
 */

/**
 * Accepted forms for a user-supplied accent colour.
 *
 * Restricting it to plain colour literals keeps anything that could
 * terminate a declaration or open a `url()` out of the stylesheet, whichever
 * path applies it. The value reaches CSS through `setProperty`, never
 * through generated stylesheet text, so a malformed value is rejected by the
 * CSSOM as well — this is the first of two gates, not the only one.
 */
const SAFE_COLOR =
  /^(#[0-9a-f]{3,8}|(rgb|hsl|oklch|lab|lch)a?\([0-9a-z%.,/\s+-]*\)|[a-z]{3,20})$/i;

/** True when `value` is a colour literal safe to use as an accent override. */
export function isSafeColor(value: string): boolean {
  const v = value.trim();
  return v.length > 0 && v.length <= 64 && SAFE_COLOR.test(v);
}
