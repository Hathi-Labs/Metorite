/**
 * The ONE short-number formatter of the product (owner, 2026-10-19).
 *
 * "When I ask for graphs and numbers, you use the million convention, which
 * is not standard in India." So a number system is a member's preference:
 *
 *   indian         1.2K · 85K · 12.5 L · 1.35 Cr   (lakh and crore)
 *   international  1.2K · 85K · 12.5M · 1.35B      (thousand, million, billion)
 *
 * The default is "indian" for every org. The preference is stored in
 * `org_settings` (key `number_system`) with a member override in
 * `user_settings.number_system`. The member override wins. The gateway
 * resolves it (`acb_common.number_system.resolve`).
 *
 * The chart engine (`charts/kinds.mjs`) and the CRM's compact money
 * (`app/crm/lib/format.ts`) both draw through `compactNumber`. Do not add a
 * second "k/M" formatter: extend this one. Plain `.mjs`, because the
 * WhatsApp PNG renderer (`charts/render.mjs`) runs it under Node with no
 * TypeScript step.
 *
 * Fence: `src/lib/numberFormat.test.ts`.
 */

/** @typedef {"indian" | "international"} NumberSystem */

/** @type {readonly NumberSystem[]} */
export const NUMBER_SYSTEMS = Object.freeze(["indian", "international"]);

/** @type {NumberSystem} */
export const DEFAULT_NUMBER_SYSTEM = "indian";

/**
 * A stored or sent value as a number system. Anything else is the default,
 * so an old spec or a bad value never breaks a chart.
 * @param {unknown} value
 * @returns {NumberSystem}
 */
export function numberSystem(value) {
  return value === "international" ? "international" : DEFAULT_NUMBER_SYSTEM;
}

/** A no-break space: "12.5 L" never breaks between the number and its unit. */
const NBSP = "\u00a0";

/**
 * Each system's bands, largest first. A band is [size, suffix]. The last
 * band (size 1) is the plain number below a thousand.
 * @type {Record<NumberSystem, ReadonlyArray<readonly [number, string]>>}
 */
const BANDS = {
  indian: [[1e7, `${NBSP}Cr`], [1e5, `${NBSP}L`], [1e3, "K"], [1, ""]],
  international: [[1e9, "B"], [1e6, "M"], [1e3, "K"], [1, ""]],
};

/**
 * A scaled value with at most three significant digits and two decimals:
 * 1.35, 12.5, 850. A plain number below a thousand keeps up to two decimals,
 * as the chart engine always drew it.
 * @param {number} x  zero or more
 * @param {number} size
 */
function rounded(x, size) {
  if (size === 1) return Number.isInteger(x) ? x : +x.toFixed(2);
  return +x.toFixed(x < 10 ? 2 : x < 100 ? 1 : 0);
}

/**
 * The digits of `n` with the system's grouping: 1,23,456 or 123,456.
 * Only the largest band reaches a value of a thousand or more.
 * @param {number} n
 * @param {NumberSystem} system
 */
function grouped(n, system) {
  return n < 1000 ? `${n}` : new Intl.NumberFormat(system === "indian" ? "en-IN" : "en-US", {
    maximumFractionDigits: 2,
  }).format(n);
}

/**
 * A number written out in full, with the system's grouping and a fixed
 * count of decimals: 12,34,567.5 (indian) or 1,234,567.5 (international).
 * A stat tile shows the exact value, so it groups and never shortens.
 * @param {number} v
 * @param {unknown} [system]  a number system; anything else is the default
 * @param {number} [decimals]  0 to 20
 * @returns {string}
 */
export function groupedNumber(v, system, decimals = 0) {
  if (!Number.isFinite(v)) return `${v}`;
  const places = Math.min(20, Math.max(0, Math.trunc(decimals) || 0));
  return new Intl.NumberFormat(numberSystem(system) === "indian" ? "en-IN" : "en-US", {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  }).format(v);
}

/**
 * A short number in the given system. The sign is an ASCII "-": the caller
 * places it (the chart engine draws the typographic minus, the CRM puts it
 * before the currency sign).
 *
 * A value that rounds up to the next band moves to that band: 99,999 is
 * "1 L" in the Indian system and never "100K". 999,999 is "1M" in the
 * international system and never "1000K".
 *
 * @param {number} v
 * @param {unknown} [system]  a number system; anything else is the default
 * @returns {string}
 */
export function compactNumber(v, system) {
  const sys = numberSystem(system);
  if (!Number.isFinite(v)) return `${v}`;
  const a = Math.abs(v);
  const bands = BANDS[sys];
  let i = bands.findIndex(([size]) => a >= size);
  if (i < 0) i = bands.length - 1; // below 1: the plain band
  let n = rounded(a / bands[i][0], bands[i][0]);
  // Rounding can reach the next band up (99,999 → 100K). Move up one band.
  if (i > 0 && n * bands[i][0] >= bands[i - 1][0]) {
    i -= 1;
    n = rounded(a / bands[i][0], bands[i][0]);
  }
  const body = `${grouped(n, sys)}${bands[i][1]}`;
  return v < 0 && n !== 0 ? `-${body}` : body;
}
