/**
 * Projects · which attachments can be PREVIEWED, and which must not be.
 *
 * Pure: no React, no DOM. The rule lives here because it is a security
 * decision as much as a design one, and because the same answer is needed by
 * the thumbnail grid, the viewer and the server's `Content-Disposition`.
 *
 * ## 🔴 Why this is a safelist and never a blocklist
 *
 * Previewing means the browser RENDERS bytes a member uploaded, on this
 * application's origin, with this application's cookies attached. Get the set
 * wrong in the permissive direction and an attachment becomes stored XSS.
 *
 * Two types are excluded on purpose, and both would otherwise look obviously
 * previewable:
 *
 * - **SVG.** `image/svg+xml` is in the upload's `_IMAGE_MIMES`, so the server
 *   already classifies it as `kind: "image"`. An SVG is a document: it may
 *   carry `<script>`, and rendered inline on our origin that script runs with
 *   the session. It downloads; it never previews.
 * - **HTML and anything text/\*.** Same reason, more obviously.
 *
 * Anything not named here downloads. A new type is added by a human deciding
 * it is safe, never by a pattern that happens to match.
 */

/** Rendered as an image, inline, on our own origin. SVG is NOT here. */
export const PREVIEWABLE_IMAGE_MIMES: readonly string[] = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "image/avif",
];

/** Rendered in a frame by the browser's own viewer. */
export const PREVIEWABLE_DOCUMENT_MIMES: readonly string[] = [
  "application/pdf",
];

export type PreviewKind = "image" | "document" | "none";

/**
 * How this attachment may be shown.
 *
 * ⚠️ Keyed on the MIME the SERVER stored, never on the file extension. A
 * member can name a file anything; the extension is a claim and the stored
 * type is what the server will send back in `Content-Type`.
 */
export function previewKind(mime: string | null | undefined): PreviewKind {
  const type = (mime ?? "").trim().toLowerCase().split(";")[0];
  if (PREVIEWABLE_IMAGE_MIMES.includes(type)) return "image";
  if (PREVIEWABLE_DOCUMENT_MIMES.includes(type)) return "document";
  return "none";
}

/** Can this attachment be shown at all, rather than only downloaded? */
export function canPreview(mime: string | null | undefined): boolean {
  return previewKind(mime) !== "none";
}

/**
 * A size a person reads, or `null` when the server did not say.
 *
 * ⚠️ `null` rather than "0 KB" or "—". `Math.round(undefined / 1024)` is NaN,
 * and the panel rendered a literal "NaN KB" at the member until 2026-09-19.
 * An unknown size says nothing; it does not guess.
 */
export function readableSize(bytes: number | null | undefined): string | null {
  if (!Number.isFinite(bytes as number) || (bytes as number) < 0) return null;
  const size = bytes as number;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
