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
 *
 * ⚠️ **Per account** (owner bug, 2026-10-11). The script reads the pointer
 * `APPEARANCE_SCOPE_KEY` first, then each value at `<key>:<scope>`. With no
 * pointer it reads the bare keys, as before. It also copies the scope's
 * colour mode into next-themes' bare `theme` key, or removes that key when the
 * scope has none. next-themes' own script runs after this one, inside
 * `Providers`, so it reads the account's mode. `scope.ts` has the rules.
 */

import { DENSITY_SCALE } from "./types";
import {
  ACCENT_INK_PROPERTIES,
  ACCENT_PROPERTIES,
  APPEARANCE_SCOPE_KEY,
  DENSITY_PROPERTY,
  MODE_STORAGE_KEY,
  SCOPE_SEPARATOR,
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
  const mode = JSON.stringify(MODE_STORAGE_KEY);
  const sep = JSON.stringify(SCOPE_SEPARATOR);

  return `(function(){try{
var d=document.documentElement,ls=window.localStorage;
var s=ls.getItem(${JSON.stringify(APPEARANCE_SCOPE_KEY)});
function k(n){return s?n+${sep}+s:n;}
if(s){var m=ls.getItem(k(${mode}));if(m)ls.setItem(${mode},m);else ls.removeItem(${mode});}
var scale=${scales};
var den=ls.getItem(k(${JSON.stringify(STORAGE_KEYS.density)}))||ls.getItem(k(${JSON.stringify(STORAGE_KEYS.orgDensity)}));
if(scale[den])d.style.setProperty(${JSON.stringify(DENSITY_PROPERTY)},String(scale[den]));
var a=ls.getItem(k(${JSON.stringify(STORAGE_KEYS.accent)}));
if(a){var p=${accentProps};for(var i=0;i<p.length;i++)d.style.setProperty(p[i],a);
var ink=ls.getItem(k(${JSON.stringify(STORAGE_KEYS.accentInk)}));if(ink){var q=${accentInkProps};for(var j=0;j<q.length;j++)d.style.setProperty(q[j],ink);}}
}catch(e){}})();`;
}
