/**
 * The logo pipeline's pure half: what to keep, how to size it, and what dark
 * mode needs. It works on raw RGBA pixels, so it is tested without a browser.
 * `logoCanvas.ts` is the browser half (decode, draw, encode).
 *
 * Why the browser does this work, not the server. An admin used to pick a
 * file and get one of six refusals back ("too large", "too tall", "SVG is not
 * accepted"…), and the save itself then failed (2026-10-08). The shell needs
 * one thing: a small PNG of the logo with no empty margin, about 28px tall at
 * 3× density. The browser can make exactly that from almost any image the
 * admin has, SVG included, so the server's bounds stop being something a
 * person meets. The server still checks every bound (it is the authority).
 */

/** The shell renders the logo this tall, in CSS px (`OrgBrandLockup`). */
export const LOGO_DISPLAY_HEIGHT = 28;
/** The widest the sidebar lets a logo render, in CSS px. */
export const LOGO_DISPLAY_MAX_WIDTH = 180;
/** Stored at 3×, so it is sharp on a phone. */
export const LOGO_DENSITY = 3;
/** The server's shape bounds (`settings.py` `_LOGO_MIN_ASPECT` and `_MAX_`). */
export const MIN_ASPECT = 0.5;
export const MAX_ASPECT = 8;

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Pixels {
  data: Uint8ClampedArray;
  width: number;
  height: number;
}

export type Background =
  | { kind: "transparent" }
  | { kind: "solid"; rgb: [number, number, number] }
  | { kind: "none" };

const at = (p: Pixels, x: number, y: number) => (y * p.width + x) * 4;

function dist(a: ArrayLike<number>, i: number, rgb: readonly number[]): number {
  return Math.max(Math.abs(a[i] - rgb[0]), Math.abs(a[i + 1] - rgb[1]), Math.abs(a[i + 2] - rgb[2]));
}

/**
 * What surrounds the logo. Transparent when a real share of the pixels are
 * clear. Solid when the four corners agree on one colour, as a JPEG export on
 * white does. Otherwise none: a photo, or a logo that fills its frame.
 */
export function detectBackground(p: Pixels): Background {
  let clear = 0;
  for (let i = 3; i < p.data.length; i += 4) if (p.data[i] < 16) clear++;
  if (clear > (p.width * p.height) / 20) return { kind: "transparent" };
  const corners = [at(p, 0, 0), at(p, p.width - 1, 0), at(p, 0, p.height - 1), at(p, p.width - 1, p.height - 1)];
  const rgb: [number, number, number] = [p.data[corners[0]], p.data[corners[0] + 1], p.data[corners[0] + 2]];
  return corners.every((c) => dist(p.data, c, rgb) <= 24) ? { kind: "solid", rgb } : { kind: "none" };
}

function isInk(p: Pixels, i: number, bg: Background): boolean {
  if (p.data[i + 3] < 16) return false;
  if (bg.kind === "solid") return dist(p.data, i, bg.rgb) > 32;
  return true;
}

/**
 * The tightest box around the logo itself, so an export with a wide empty
 * margin does not render as a small logo in a large empty slot. The whole
 * image when nothing stands out from the background.
 */
export function contentBounds(p: Pixels, bg: Background = detectBackground(p)): Box {
  if (bg.kind === "none") return { x: 0, y: 0, w: p.width, h: p.height };
  let x0 = p.width, y0 = p.height, x1 = -1, y1 = -1;
  for (let y = 0; y < p.height; y++) {
    for (let x = 0; x < p.width; x++) {
      if (!isInk(p, at(p, x, y), bg)) continue;
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
    }
  }
  if (x1 < 0) return { x: 0, y: 0, w: p.width, h: p.height };
  return { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
}

/**
 * Grow the box, centred, until its shape fits the server's bounds. A wordmark
 * longer than 8:1 gains a little height, a tall mark a little width. The new
 * area is transparent, so a person never meets "That shape will not fit".
 */
export function fitAspect(box: Box, min = MIN_ASPECT, max = MAX_ASPECT): Box {
  const r = box.w / box.h;
  if (r > max) {
    const h = Math.ceil(box.w / max);
    return { x: box.x, y: box.y - (h - box.h) / 2, w: box.w, h };
  }
  if (r < min) {
    const w = Math.ceil(box.h * min);
    return { x: box.x - (w - box.w) / 2, y: box.y, w, h: box.h };
  }
  return box;
}

/**
 * The output size in px: the slot's height at 3×, never wider than the slot.
 * ⚠️ Rounding can push an exact 8:1 box to 540×67, which is 8.06 and refused.
 * So the short side grows by a pixel when the rounded shape passes a bound.
 */
export function outputSize(box: Box, density = LOGO_DENSITY): { width: number; height: number } {
  const maxH = LOGO_DISPLAY_HEIGHT * density;
  const maxW = LOGO_DISPLAY_MAX_WIDTH * density;
  const scale = Math.min(maxH / box.h, maxW / box.w);
  let width = Math.max(1, Math.round(box.w * scale));
  let height = Math.max(1, Math.round(box.h * scale));
  if (width / height > MAX_ASPECT) height = Math.ceil(width / MAX_ASPECT);
  if (width / height < MIN_ASPECT) width = Math.ceil(height * MIN_ASPECT);
  return { width, height };
}

/**
 * Make a solid background clear, with a soft edge so the logo keeps its
 * anti-aliasing. Pixels near the background colour fade out in proportion.
 */
export function removeSolid(p: Pixels, rgb: readonly number[], hard = 18, soft = 60): void {
  for (let i = 0; i < p.data.length; i += 4) {
    const d = dist(p.data, i, rgb);
    if (d <= hard) p.data[i + 3] = 0;
    else if (d < soft) p.data[i + 3] = Math.round((p.data[i + 3] * (d - hard)) / (soft - hard));
  }
}

/** The dark sidebar's luminance, near black (`--sidebar-background` dark). */
export const DARK_SIDEBAR_LUMINANCE = 0.008;

function saturationOf(r: number, g: number, b: number): number {
  const mx = Math.max(r, g, b);
  return mx === 0 ? 0 : (mx - Math.min(r, g, b)) / mx;
}

/** Too dark to read on the dark sidebar (WCAG 3:1, the bar for graphics). */
function lowContrast(r: number, g: number, b: number, bg = DARK_SIDEBAR_LUMINANCE): boolean {
  return contrast(luminance(r, g, b), bg) < 3;
}

/**
 * The dark-mode white version: every DARK, NEUTRAL pixel turns white, alpha
 * kept. A colour that already reads (an orange mark) stays as it is, so black
 * text beside a brand mark becomes white text beside the same brand mark.
 * Measured 2026-10-09: a whole-logo recolour turned a two-colour logo into a
 * white silhouette and lost the brand.
 */
export function toWhite(p: Pixels): void {
  for (let i = 0; i < p.data.length; i += 4) {
    if (p.data[i + 3] === 0) continue;
    const r = p.data[i], g = p.data[i + 1], b = p.data[i + 2];
    if (!lowContrast(r, g, b) || saturationOf(r, g, b) >= 0.35) continue;
    p.data[i] = 255;
    p.data[i + 1] = 255;
    p.data[i + 2] = 255;
  }
}

/** Relative luminance of an sRGB colour, 0 to 1 (WCAG). */
export function luminance(r: number, g: number, b: number): number {
  const lin = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function contrast(a: number, b: number): number {
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

export interface InkStats {
  /** The share of the logo's ink too dark to read on the dark sidebar. */
  lowShare: number;
  /** How colourful that dark ink is, 0 (black, grey) to 1. */
  lowSaturation: number;
}

/**
 * The logo's ink, read for dark mode. ⚠️ NOT an average of the whole logo:
 * a bright orange mark lifts the average while navy text beside it still
 * vanishes on the dark sidebar (measured 2026-10-09). So it counts the share
 * of the ink that is too dark, and how colourful that part is.
 */
export function inkStats(p: Pixels): InkStats {
  let ink = 0, low = 0, lowSat = 0;
  for (let i = 0; i < p.data.length; i += 4) {
    if (p.data[i + 3] < 128) continue;
    const r = p.data[i], g = p.data[i + 1], b = p.data[i + 2];
    ink++;
    if (lowContrast(r, g, b)) {
      low++;
      lowSat += saturationOf(r, g, b);
    }
  }
  return { lowShare: ink === 0 ? 0 : low / ink, lowSaturation: low === 0 ? 0 : lowSat / low };
}

/** How the logo should look in dark mode. Stored with the logo (`darkStyle`). */
export type DarkStyle = "same" | "white" | "plate";

/**
 * What dark mode needs, from the logo's own ink. A logo that reads on the
 * dark sidebar stays as it is: under 15% of its ink is too dark. When the
 * dark part is black or grey (text, most often) it turns white and the rest
 * keeps its colours. When the dark part is itself a colour (navy, maroon),
 * the logo keeps all its colours on a small light card.
 */
export function adviseDarkStyle(ink: InkStats): DarkStyle {
  if (ink.lowShare < 0.15) return "same";
  return ink.lowSaturation < 0.35 ? "white" : "plate";
}
