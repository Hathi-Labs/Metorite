/**
 * Organisation branding — the rules, the wording, and the lockup decision.
 *
 * A customer uploads their logo in Settings → Organization and it replaces our
 * mark in the top-left of the shell, with "powered by Metorite" beneath
 * it. This module owns the parts of that with a right and a wrong answer, so
 * they can be tested without a browser or a gateway.
 *
 * ⚠️ **Nothing here is a security boundary.** The limits below are duplicated
 * from `apps/services/gateway/gateway/routes/settings.py`, which is the
 * authority: it re-derives the format from the file's magic bytes and re-checks
 * every bound. This copy exists only so that picking a 4 MB photo says so
 * immediately instead of after the upload — a client-side check a caller can
 * skip is a courtesy, never a fence. If the two disagree, the server wins and
 * the page shows what it said.
 *
 * Dimensions are deliberately *not* checked here: reading them means decoding
 * the image, and the honest place to decode an image is the one that is going
 * to store it.
 */

/** A stored logo, exactly as the gateway returns it. */
export interface OrgLogo {
  /** `data:image/png;base64,…` — rebuilt server-side from the sniffed type. */
  dataUri: string;
  mime: string;
  width: number;
  height: number;
  byteSize: number;
}

export interface OrgBranding {
  /** `null` means no logo uploaded — a different state from "gateway down". */
  logo: OrgLogo | null;
  updatedBy: string;
  updatedAt: string;
}

/** Mirrors `_LOGO_MAX_BYTES`. See the warning above. */
export const LOGO_MAX_BYTES = 128 * 1024;

/**
 * The `accept` attribute for the file input.
 *
 * SVG is absent on purpose and the reason is worth keeping next to the list:
 * an SVG is a document that can carry script and external references, stored
 * by one tenant and rendered in every colleague's shell. A 28px-tall header
 * slot does not need vector art enough to take that on.
 */
export const LOGO_ACCEPT = "image/png,image/jpeg,image/webp";

const ACCEPTED_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

/** What the upload panel tells an admin before they open the picker. */
export const LOGO_RULES: readonly string[] = [
  "PNG, JPEG or WebP — PNG with a transparent background looks best",
  "At least 32px and at most 2048px on the longer side",
  "Wider than tall reads best; up to 8:1 is accepted",
  `Under ${Math.round(LOGO_MAX_BYTES / 1024)} KB`,
];

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  return kb < 1024 ? `${Math.round(kb)} KB` : `${(kb / 1024).toFixed(1)} MB`;
}

/**
 * An advisory check on the file the admin picked. Returns an error message, or
 * `null` to mean "worth sending" — never "valid", which only the server decides.
 */
export function precheckLogoFile(file: { type: string; size: number }): string | null {
  if (file.type === "image/svg+xml") {
    // Named rather than folded into "unsupported": SVG is what a designer
    // hands over, so this is the single most likely rejection, and an admin
    // who is told to export a PNG is unblocked in one step.
    return "SVG logos are not accepted. Please export your logo as a PNG, at 2× or 3× the display size.";
  }
  if (!ACCEPTED_TYPES.has(file.type)) {
    return "Please choose a PNG, JPEG or WebP image.";
  }
  if (file.size === 0) return "That file is empty.";
  if (file.size > LOGO_MAX_BYTES) {
    return `That image is ${formatBytes(file.size)}; the limit is ${formatBytes(LOGO_MAX_BYTES)}.`;
  }
  return null;
}

/**
 * What the top-left of the shell should render.
 *
 * Two states, and the fallback is the interesting one: an organisation that has
 * not uploaded anything must get our own mark deliberately, not an empty box
 * where a logo would be. Every caller therefore gets a complete answer and none
 * of them has to write `branding?.logo ? … : …` for itself — which is how the
 * desktop sidebar and the mobile menu would drift apart.
 */
export type Lockup =
  | { kind: "org"; logo: OrgLogo; alt: string; caption: string }
  | { kind: "default"; title: string; caption: string };

/** The caption under a customer's logo. One string, one place. */
export const POWERED_BY = "powered by Metorite";

export function lockup(
  branding: OrgBranding | null | undefined,
  fallbackCaption: string,
): Lockup {
  const logo = branding?.logo;
  if (!logo?.dataUri) {
    return { kind: "default", title: "Metorite", caption: fallbackCaption };
  }
  return {
    kind: "org",
    logo,
    // Not "logo" alone: a screen reader reaching the top of the app should
    // hear whose product this is, and the link it sits in is the way home.
    alt: "Your organization's logo",
    caption: POWERED_BY,
  };
}

/**
 * How wide the logo may render, given the height the slot allows.
 *
 * The image is constrained on BOTH axes — height so it matches the row it sits
 * in, width so a wide wordmark cannot push the sidebar's collapse control off
 * the edge. Scaling by the true aspect ratio keeps a square mark from being
 * allotted a wordmark's width and floating in the gap.
 */
// ── The first-paint cache (OI-3a) ──────────────────────────────────────────
//
// Without this the shell renders OUR mark on every page load and swaps to the
// customer's ~900ms later once `/api/settings/branding` answers — measured in
// Chromium against a mocked API, so a real deployment is worse. That inverts
// the feature: the customer's own members see our branding first, every time.
//
// The theming engine already solved exactly this. `theme/storage.ts` caches the
// org's theme so a member "gets the company look on first paint instead of a
// flash of the built-in default". Same problem, same answer: read from storage
// first, revalidate over the network behind it.
//
// ⚠️ **This does NOT make the SERVER-rendered HTML carry the logo** — that is
// OI-3b and needs the root layout to fetch branding server-side. What it
// removes is the network round-trip: the swap becomes one frame after
// hydration rather than a visible flash.

/** Versioned, so a shape change invalidates rather than half-parses. */
const BRANDING_CACHE_KEY = "cc-org-branding-v1";

/**
 * ⚠️ **`localStorage` is not a trust boundary.** Anything that has ever run on
 * this origin can write it, so a value read back out is untrusted input in
 * exactly the way a network response is — and this one becomes an `<img src>`.
 * The server sniffs magic bytes to decide the MIME type; the least this side
 * can do is refuse a URI that is not a `data:image/`.
 */
export function isRenderableLogoUri(uri: unknown): uri is string {
  return typeof uri === "string" && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(uri);
}

export function readCachedBranding(store: Pick<Storage, "getItem"> | undefined): OrgBranding | null {
  try {
    const raw = store?.getItem(BRANDING_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as OrgBranding;
    // A cached "no logo" is a legitimate, useful answer — it means render our
    // mark immediately rather than waiting to find out.
    if (parsed?.logo == null) return { logo: null, updatedBy: "", updatedAt: "" };
    if (!isRenderableLogoUri(parsed.logo.dataUri)) return null;
    return parsed;
  } catch {
    // Corrupt JSON, blocked storage, private mode: fall back to the network.
    return null;
  }
}

export function writeCachedBranding(
  store: Pick<Storage, "setItem" | "removeItem"> | undefined,
  branding: OrgBranding | null,
): void {
  try {
    if (!branding) store?.removeItem(BRANDING_CACHE_KEY);
    else store?.setItem(BRANDING_CACHE_KEY, JSON.stringify(branding));
  } catch {
    // Quota exceeded or storage unavailable — the feature still works, it just
    // costs a round-trip. Never let a cache write break a render.
  }
}

export function logoBoxWidth(logo: OrgLogo, maxHeight: number, maxWidth: number): number {
  if (logo.width <= 0 || logo.height <= 0) return maxWidth;
  const scaled = Math.round((logo.width / logo.height) * maxHeight);
  return Math.min(scaled, maxWidth);
}
