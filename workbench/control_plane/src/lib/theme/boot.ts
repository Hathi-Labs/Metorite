/**
 * Pre-paint boot script.
 *
 * Runs before React hydrates and before the first paint, so the document
 * already carries the member's density and accent when pixels first hit the
 * screen. Without it every load would flash the defaults for a frame before
 * the store caught up — the same problem next-themes solves for light/dark,
 * applied to the two axes it does not know about.
 *
 * ⚠️ The theme axis is gone (owner directive 2026-08-31). The script no
 * longer resolves or applies a `data-theme`, because there is one look and
 * `globals.css` carries it — nothing to flash between.
 *
 * Constraints: this string is executed as an inline `<script>`, so it cannot
 * import anything and must not throw. Storage keys are interpolated from
 * `storage.ts` so the script and the React store always read the same places.
 * It contains no server- or user-supplied data — the values it acts on come
 * from localStorage and are written through the CSSOM, never concatenated into
 * CSS text.
 */

import { DENSITY_SCALE } from "./types";
import {
  ACCENT_INK_PROPERTIES,
  ACCENT_PROPERTIES,
  DENSITY_PROPERTY,
  STORAGE_KEYS,
} from "./storage";

/**
 * The script source. Generated rather than hand-written so the density scale
 * and storage keys stay in lockstep with the modules that own them.
 */
export function themeBootScript(): string {
  const scales = JSON.stringify(DENSITY_SCALE);
  const accentProps = JSON.stringify(ACCENT_PROPERTIES);
  const accentInkProps = JSON.stringify(ACCENT_INK_PROPERTIES);

  return `(function(){try{
var d=document.documentElement,ls=window.localStorage;
var scale=${scales};
var den=ls.getItem(${JSON.stringify(STORAGE_KEYS.density)})||ls.getItem(${JSON.stringify(STORAGE_KEYS.orgDensity)});
if(scale[den])d.style.setProperty(${JSON.stringify(DENSITY_PROPERTY)},String(scale[den]));
var a=ls.getItem(${JSON.stringify(STORAGE_KEYS.accent)});
if(a){var p=${accentProps};for(var i=0;i<p.length;i++)d.style.setProperty(p[i],a);
var ink=ls.getItem(${JSON.stringify(STORAGE_KEYS.accentInk)});if(ink){var q=${accentInkProps};for(var j=0;j<q.length;j++)d.style.setProperty(q[j],ink);}}
}catch(e){}})();`;
}
