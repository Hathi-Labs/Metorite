/**
 * The look (D149, `project-docs/specs/glass_look.md` §4).
 *
 * Glass is the only look. It applies through ONE attribute on <html>, which
 * the server renders on every page. No build flag, no member choice and no
 * boot script can take it off. Every glass rule in `globals.css` and every
 * `glass:` class keys off it.
 *
 * ⚠️ The old build flag is dead. A box that still sets it does no harm,
 * because nothing reads it. GL-Z2 folds the `glass:` classes into plain
 * classes and removes the classic token blocks. `Look` stays until then,
 * because the token helpers still take a classic value.
 *
 * Fence: `glassLook.test.ts`.
 */

export type Look = "classic" | "glass";

/** The attribute on <html> that carries the look. */
export const LOOK_ATTRIBUTE = "data-look";

/** The attribute's one value. */
export const GLASS_LOOK = "glass";
