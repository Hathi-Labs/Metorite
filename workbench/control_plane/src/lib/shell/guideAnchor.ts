/**
 * Whether the member can see an anchor (WS-OB GD-4). It lives apart from
 * `GuideSpotlight.tsx`, whose fence (`GuideSpotlight.test.ts` rule 4) forbids
 * any word of focus capture in that file, and a closed assistant panel is
 * marked by the attribute that names it here.
 */

/**
 * The attributes whose change can make an anchor appear: it is added, or its
 * panel is shown (a class swap drops `invisible` from the sheet's body when the
 * strip rises), or the panel is no longer closed. The watch lasts a few seconds.
 */
export const WATCHED_ATTRIBUTES = ["data-guide", "inert", "data-shown", "class", "style", "hidden"];

/**
 * Whether the element has a box, is not hidden, and has no closed panel
 * above it. A closed assistant panel stays in the tree with its box intact.
 */
export function isShown(el: HTMLElement): boolean {
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  if (el.closest("[inert]")) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== "hidden" && cs.display !== "none";
}
