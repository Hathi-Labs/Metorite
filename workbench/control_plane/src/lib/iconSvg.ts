/**
 * iconSvg — render an icon (by Lucide name) to a static SVG STRING.
 *
 * The sandbox runs in an isolated origin and cannot import from our bundle, so
 * it can't use React <Icon> components. Instead the parent resolves the icons an
 * agent asked for into inline SVG markup here and injects those strings into the
 * frame. SVG uses stroke="currentColor", so an injected icon inherits the
 * surrounding CSS color with no extra wiring.
 *
 * Icons are data (SVG), not code — injecting them keeps the sandbox's no-network
 * guarantee intact (no CDN, no <img> host to allow-list).
 *
 * ⚠️ **The pack argument is gone** (owner directive 2026-08-31). This file used
 * to take a `pack` and consult the Iconify collection, because an icon set was
 * part of a theme. With one theme there is one pack, Lucide, and a sandboxed
 * app draws the same glyphs as the shell around it by construction rather than
 * by threading a parameter through three layers.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { resolveIcon } from "@/lib/icons";

/** Render one Lucide icon to an SVG string. Returns "" for a bad name (caller
 *  decides the fallback). Size in px; stroke inherits `currentColor`. */
export function iconToSvg(name: string, size = 18): string {
  try {
    const Icon = resolveIcon(name);
    return renderToStaticMarkup(createElement(Icon, { size, strokeWidth: 1.75 }));
  } catch {
    return "";
  }
}

/** Build a { name → svgString } map for a list of requested icon names.
 *  De-dupes and caps the count so a runaway list can't bloat a frame. */
export function buildIconMap(
  names: unknown,
  size = 18,
  cap = 40,
): Record<string, string> {
  if (!Array.isArray(names)) return {};
  const out: Record<string, string> = {};
  for (const raw of names.slice(0, cap)) {
    const name = typeof raw === "string" ? raw : String(raw ?? "");
    if (!name || out[name]) continue;
    const svg = iconToSvg(name, size);
    if (svg) out[name] = svg;
  }
  return out;
}

/** Re-exported so callers that resolve icons import from one place. The scanner
 *  itself lives in iconRefs.ts, free of any React dependency. */
export { iconsUsedIn } from "@/lib/iconRefs";
