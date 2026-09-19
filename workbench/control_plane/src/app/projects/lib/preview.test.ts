/**
 * Which attachments may be PREVIEWED (WS-27i follow-up, 2026-09-19).
 *
 * 🔴 These are security cases wearing design clothes. Previewing renders
 * member-uploaded bytes on this application's origin with the member's
 * session attached, so the set is a safelist and every case below is a way
 * it could be wrong in the permissive direction.
 */

import { describe, expect, it } from "vitest";

import {
  PREVIEWABLE_IMAGE_MIMES,
  canPreview,
  previewKind,
  readableSize,
} from "./preview";

describe("previewKind", () => {
  it("shows the ordinary raster images", () => {
    for (const mime of PREVIEWABLE_IMAGE_MIMES) {
      expect(previewKind(mime)).toBe("image");
    }
  });

  it("shows a PDF as a document", () => {
    expect(previewKind("application/pdf")).toBe("document");
  });

  it("🔴 NEVER previews an SVG, although the server calls it an image", () => {
    // The upload's `_IMAGE_MIMES` includes `image/svg+xml`, so the row comes
    // back as `kind: "image"`. An SVG is a DOCUMENT that may carry
    // `<script>`, and rendered inline on our origin that script runs with
    // the session cookie. It downloads.
    expect(previewKind("image/svg+xml")).toBe("none");
    expect(canPreview("image/svg+xml")).toBe(false);
  });

  it("🔴 never previews anything text, which includes HTML", () => {
    for (const mime of ["text/html", "text/plain", "application/xhtml+xml"]) {
      expect(previewKind(mime)).toBe("none");
    }
  });

  it("refuses what it does not recognise, rather than guessing", () => {
    for (const mime of ["", null, undefined, "application/octet-stream", "video/mp4"]) {
      expect(previewKind(mime)).toBe("none");
    }
  });

  it("reads a Content-Type with parameters and odd case", () => {
    // `image/PNG; charset=binary` is a real thing a server sends.
    expect(previewKind("image/PNG; charset=binary")).toBe("image");
    expect(previewKind("  application/pdf  ")).toBe("document");
  });

  it("⚠️ a prefix match must not let an SVG through", () => {
    // The tempting implementation is `mime.startsWith("image/")`, which
    // previews every SVG ever uploaded.
    expect(previewKind("image/svg+xml; charset=utf-8")).toBe("none");
  });
});

describe("readableSize", () => {
  it("scales through the units", () => {
    expect(readableSize(512)).toBe("512 B");
    expect(readableSize(2048)).toBe("2 KB");
    expect(readableSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("⚠️ says NOTHING when the server did not send a size", () => {
    // The panel rendered a literal "NaN KB" at the member until 2026-09-19,
    // because `Math.round(undefined / 1024)` is NaN.
    for (const bad of [undefined, null, NaN, -1]) {
      expect(readableSize(bad as number)).toBeNull();
    }
  });

  it("never rounds a real file down to zero", () => {
    expect(readableSize(1)).toBe("1 B");
    expect(readableSize(1025)).toBe("1 KB");
  });
});
