/**
 * How the reading pane draws an HTML email body in dark mode (owner, 2026-10-10).
 *
 * The owner said: "the email body shows a white background in dark mode ... The
 * text and images should adapt to a dark background." The body draws in a
 * sandboxed iframe (`MessageContent.tsx`), so it inherits none of the app's
 * tokens. This module picks a look for the frame and builds the CSS of that look.
 *
 * Four looks:
 *
 * 1. `native` — the email declares its own dark support: a
 *    `prefers-color-scheme: dark` media query, or a `color-scheme` meta tag or
 *    CSS property that names `dark` on a mail with no colours of its own. The
 *    sender's own dark design applies (`forceDarkMedia`). Nothing is inverted.
 * 2. `tokens` — simple HTML with no colour and no background of its own. The
 *    frame takes the app's card, ink, link and border tokens.
 * 3. `invert` — styled HTML with its own colours or backgrounds. The whole
 *    document gets `invert(1) hue-rotate(180deg)`, and each image, picture,
 *    video and background image gets the same filter again, so it looks as
 *    sent. A layer multiplies the page by a base colour before the filter,
 *    so the email's white lands on the app's card colour, not on black.
 * 4. `original` — light mode, or the member asked for the light version. The
 *    frame draws exactly what it drew before this module existed.
 *
 * ⚠️ **Security.** The CSS here holds fixed text and colours made from numbers.
 * Nothing from the email reaches it. The classifier reads the email and gives
 * back one of three names, and that name is all that leaves it.
 *
 * ⚠️ **Print.** Each rule of a dark look sits inside `@media screen`. A print of
 * the page therefore draws the original light sheet.
 *
 * Fences: `bodyLook.test.ts` (the classifier, the re-invert rule, the base
 * colour and the print rule) and `e2e/email-message-actions.spec.ts` (the look
 * that a real frame gets).
 */

import { parseColor, type Rgb } from "@/lib/theme/contrast";
import { THEME } from "@/lib/theme/themes";

/** What the dark-mode classifier can decide for an HTML body. */
export type DarkRule = "native" | "tokens" | "invert";

/** The look the frame draws. `original` is the light sheet, as sent. */
export type BodyLook = "original" | DarkRule;

/** A `prefers-color-scheme: dark` media query, in a `<style>` or a `media=`. */
const DARK_MEDIA = /prefers-color-scheme\s*:\s*dark/i;

/** `<meta name="color-scheme" content="light dark">`, either attribute order. */
const DARK_META =
  /<meta\b(?=[^>]*\bname\s*=\s*["']?(?:supported-)?color-schemes?\b)(?=[^>]*\bcontent\s*=\s*["'][^"']*\bdark\b)[^>]*>/i;

/** The CSS property: `color-scheme: light dark` or `supported-color-schemes`. */
const DARK_PROPERTY = /(?<![\w-])(?:supported-)?color-schemes?\s*:\s*[^;{}"'<]*\bdark\b/i;

/**
 * A colour or a background that the email sets itself, in an attribute or in
 * CSS. A value that sets nothing (`inherit`, `transparent`, `none` …) does
 * not count. `border-color` and `color-scheme` do not count either.
 */
const OWN_COLOUR_ATTR = /<[a-z][^>]*\s(?:bgcolor|background|color|text|link|vlink|alink)\s*=/i;
const OWN_COLOUR_CSS =
  /(?<![\w-])(?:background(?:-color|-image)?|color)\s*:\s*(?!(?:inherit|initial|unset|revert|transparent|none|currentcolor)\b)[^;"'}\s]/i;

/**
 * Pick the dark-mode rule for an HTML body. The order is the owner's order:
 * the email's own dark design first, then the tokens, then the invert.
 *
 * A dark media query is a dark design. A `color-scheme` that names dark,
 * with no media query, says only that the browser's own dark colours are
 * fine. That holds for a mail with no colours of its own. A mail that sets
 * dark text on a white box would draw that text on the dark card, so it is
 * inverted.
 */
export function classifyEmailHtml(html: string): DarkRule {
  if (DARK_MEDIA.test(html)) return "native";
  const ownColours = OWN_COLOUR_ATTR.test(html) || OWN_COLOUR_CSS.test(html);
  if ((DARK_META.test(html) || DARK_PROPERTY.test(html)) && !ownColours) return "native";
  return ownColours ? "invert" : "tokens";
}

/** A query that is always true on a screen, and one that is never true. */
const ALWAYS = "(min-width: 0px)";
const NEVER = "(max-width: -1px)";

/** One media query list, with the dark condition on and the light one off. */
function forceDarkQueries(list: string): string {
  return list
    .split(",")
    .map((query) => {
      if (!/prefers-color-scheme/i.test(query)) return query;
      const dark = /prefers-color-scheme\s*:\s*dark/i.test(query);
      let out = query
        .replace(/\(\s*prefers-color-scheme\s*:\s*dark\s*\)/gi, ALWAYS)
        .replace(/\(\s*prefers-color-scheme\s*:\s*light\s*\)/gi, NEVER);
      // A forced dark rule stays off paper: a print draws the light design.
      if (dark && !/^\s*(?:only\s+|not\s+)?(?:all|screen|print|speech)\b/i.test(out)) {
        out = ` screen and ${out.trim()}`;
      }
      return out;
    })
    .join(",");
}

/**
 * Turn the sender's own dark media queries on, for the `native` look.
 *
 * A child frame takes `prefers-color-scheme` from the OS, not from the app,
 * so a member in the app's dark mode on a light OS never saw the sender's
 * dark design. Measured in Chromium on 2026-10-10: the frame element's
 * `color-scheme: dark` does not reach the child's media query.
 *
 * It runs on the SANITIZED markup. It swaps a media condition for fixed
 * words with no `<`, so it cannot make a tag, and it adds no text of its own
 * beyond those words.
 */
export function forceDarkMedia(html: string): string {
  return html
    .replace(/@media([^{;]*)\{/gi, (whole, list: string) =>
      /prefers-color-scheme/i.test(list) ? `@media${forceDarkQueries(list).trimEnd()} {` : whole,
    )
    .replace(/(<style\b[^>]*?\bmedia\s*=\s*)(["'])([^"']*)\2/gi, (whole, head: string, q: string, list: string) =>
      /prefers-color-scheme/i.test(list) ? `${head}${q}${forceDarkQueries(list).trim()}${q}` : whole,
    );
}

/**
 * The look of one frame. Light mode, and the member's light version, always
 * get the original.
 */
export function chooseBodyLook(input: {
  dark: boolean;
  lightVersion: boolean;
  html: string;
}): BodyLook {
  if (!input.dark || input.lightVersion) return "original";
  return classifyEmailHtml(input.html);
}

/** The colour scheme the iframe ELEMENT carries. A child frame reads its
 *  `prefers-color-scheme` from it, so only `native` says dark. */
export function frameColorScheme(look: BodyLook): "light" | "dark" {
  return look === "native" ? "dark" : "light";
}

// ── The base colour of the invert ──────────────────────────────────────────

/** The filter of the invert look, and of each re-inverted image. */
export const INVERT_FILTER = "invert(1) hue-rotate(180deg)";

/**
 * The hue-rotate matrix of the Filter Effects spec, for `deg` degrees. It
 * works on sRGB values in 0..1, as a browser applies a CSS filter function.
 */
export function hueRotateMatrix(deg: number): number[][] {
  const r = (deg * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [
    [0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928],
    [0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.14, 0.072 - c * 0.072 - s * 0.283],
    [0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072],
  ];
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

function apply(m: number[][], { r, g, b }: Rgb): Rgb {
  return {
    r: clamp01(m[0][0] * r + m[0][1] * g + m[0][2] * b),
    g: clamp01(m[1][0] * r + m[1][1] * g + m[1][2] * b),
    b: clamp01(m[2][0] * r + m[2][1] * g + m[2][2] * b),
  };
}

const invert = ({ r, g, b }: Rgb): Rgb => ({ r: 1 - r, g: 1 - g, b: 1 - b });

/** What {@link INVERT_FILTER} does to one colour. */
export function invertColour(c: Rgb): Rgb {
  return apply(hueRotateMatrix(180), invert(c));
}

/**
 * The base colour that {@link INVERT_FILTER} turns into `target`. A
 * hue-rotate of 180 degrees is its own inverse, so the base is
 * `1 - M180 · target`.
 */
export function invertBase(target: Rgb): Rgb {
  return invert(apply(hueRotateMatrix(180), target));
}

// ── The palette, from the tokens ────────────────────────────────────────────

/** The token colours a dark look needs, as normalised RGB. */
export interface BodyPalette {
  card: Rgb;
  ink: Rgb;
  link: Rgb;
  muted: Rgb;
  border: Rgb;
}

/** Each palette slot: the CSS custom property, then the fallback in THEME. */
const SLOTS: Record<keyof BodyPalette, [string, keyof typeof THEME.colors.dark]> = {
  card: ["--card", "card"],
  ink: ["--card-foreground", "cardForeground"],
  link: ["--primary", "primary"],
  muted: ["--muted-foreground", "mutedForeground"],
  border: ["--border", "border"],
};

/**
 * The dark palette. `read` gives the live value of a custom property, so a
 * changed accent reaches the links. A value that does not parse falls back to
 * the dark mode of `THEME`, the mirror of `globals.css`.
 */
export function bodyPalette(read: (name: string) => string | null = () => null): BodyPalette {
  const out = {} as BodyPalette;
  for (const key of Object.keys(SLOTS) as (keyof BodyPalette)[]) {
    const [prop, fallback] = SLOTS[key];
    const live = read(prop);
    out[key] =
      (live ? parseColor(live) : null) ??
      (parseColor(String(THEME.colors.dark[fallback])) as Rgb);
  }
  return out;
}

/** A colour for CSS, built from numbers only. */
export function cssColour({ r, g, b }: Rgb): string {
  const part = (v: number) => Math.round(clamp01(v) * 255).toString(16).padStart(2, "0");
  return "#" + part(r) + part(g) + part(b);
}

// ── The CSS of each look ────────────────────────────────────────────────────

/** Media that draws a picture. Each gets the filter again, so it looks as sent. */
const MEDIA = "img, picture, video, canvas, svg image";

/** An element whose background is a picture. */
const PICTURE_BACKGROUND =
  '[background], [background-image], [style*="background-image" i], [style*="url(" i]';

/**
 * The re-invert rules of the invert look. A picture inside a re-inverted box
 * gets no filter of its own, because two re-inverts would invert it again.
 */
export const REINVERT_CSS =
  `${MEDIA}, ${PICTURE_BACKGROUND} { filter: ${INVERT_FILTER}; }\n` +
  `:is(${PICTURE_BACKGROUND}, picture) :is(${MEDIA}) { filter: none; }`;

/** A remote picture while the images are blocked: its alt text gets no re-invert. */
export const BLOCKED_REMOTE_CSS = 'img[src^="http" i] { filter: none; }';

/**
 * The CSS that a look adds after the frame's own base CSS. `original` adds
 * nothing, so light mode is unchanged. Each rule sits in `@media screen`.
 */
export function bodyLookCss(
  look: BodyLook,
  palette: BodyPalette,
  opts: { remoteBlocked?: boolean } = {},
): string {
  if (look === "original") return "";
  const card = cssColour(palette.card);
  const ink = cssColour(palette.ink);
  const link = cssColour(palette.link);
  if (look === "native") {
    // The sender's dark CSS comes later in the document, so it wins where it
    // sets a colour. The card and the ink fill what it leaves unset.
    return (
      "@media screen {\n" +
      "  :root { color-scheme: dark; }\n" +
      `  html, body { background: ${card}; color: ${ink}; }\n` +
      `  a { color: ${link}; }\n` +
      "}"
    );
  }
  if (look === "tokens") {
    return (
      "@media screen {\n" +
      "  :root { color-scheme: dark; }\n" +
      `  html, body { background: ${card}; color: ${ink}; }\n` +
      `  a { color: ${link}; }\n` +
      `  blockquote { border-left-color: ${cssColour(palette.border)}; color: ${cssColour(palette.muted)}; }\n` +
      "}"
    );
  }
  // The sheet stays the white of the original, and a layer multiplies every
  // pixel by the base before the filter runs. White becomes the base, and
  // the filter turns the base into the card: the email's own white boxes
  // land on the card, not on black. Black text stays black, so it still
  // turns white. A picture loses only its deepest shadow, which lifts to the
  // card colour.
  const sheet = cssColour({ r: 1, g: 1, b: 1 });
  const base = cssColour(invertBase(palette.card));
  return (
    "@media screen {\n" +
    `  html { filter: ${INVERT_FILTER}; background: ${sheet}; position: relative; min-height: 100%; }\n` +
    `  html::after { content: ""; position: absolute; inset: 0; background: ${base}; ` +
    "mix-blend-mode: multiply; pointer-events: none; z-index: 2147483647; }\n" +
    `  ${REINVERT_CSS.replace(/\n/g, "\n  ")}\n` +
    // A blocked remote picture never loads, and its alt text draws in its
    // place. A re-invert would turn that text dark on the dark card.
    (opts.remoteBlocked ? `  ${BLOCKED_REMOTE_CSS}\n` : "") +
    "}"
  );
}
