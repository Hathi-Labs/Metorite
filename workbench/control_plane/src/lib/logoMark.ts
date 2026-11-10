"use client";

/**
 * The organization's square mark, for the folded sidebar (owner, 2026-11-01:
 * "optimise the view of the logo in square format and rectangular format ...
 * have the AI optimise or the user upload all the versions").
 *
 * The admin may upload a mark (`logoMark`), and the editor stores the one it
 * made when they do not. An organization that saved its logo before the mark
 * existed has none, so the browser makes one here from the stored logo, once
 * per image: `markBox` finds the symbol, and a 96 px square is drawn from it.
 */

import { useEffect, useState } from "react";

import { detectBackground, markBox, type Pixels } from "./logoImage";

/** The drawn mark's edge, in px: a 32 px slot at 3×. */
const SIZE = 96;

const cache = new Map<string, string | null>();
// A data URI can be 170 KB. Its length and its tail tell two logos apart.
const keyOf = (uri: string) => `${uri.length}:${uri.slice(-64)}`;

/** The mark drawn from a stored logo, or `null` when the image cannot be read. */
export async function deriveMark(dataUri: string): Promise<string | null> {
  const key = keyOf(dataUri);
  if (cache.has(key)) return cache.get(key) ?? null;
  let uri: string | null = null;
  try {
    const img = new Image();
    img.src = dataUri;
    await img.decode();
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    const src = document.createElement("canvas");
    src.width = w;
    src.height = h;
    const sctx = src.getContext("2d", { willReadFrequently: true });
    if (w > 0 && h > 0 && sctx) {
      sctx.drawImage(img, 0, 0);
      const data = sctx.getImageData(0, 0, w, h);
      const px: Pixels = { data: data.data, width: w, height: h };
      const box = markBox(px, detectBackground(px));
      const out = document.createElement("canvas");
      out.width = SIZE;
      out.height = SIZE;
      const ctx = out.getContext("2d");
      if (ctx) {
        ctx.imageSmoothingQuality = "high";
        const s = SIZE / box.w;
        ctx.drawImage(src, -box.x * s, -box.y * s, w * s, h * s);
        uri = out.toDataURL("image/png");
      }
    }
  } catch {
    uri = null;
  }
  cache.set(key, uri);
  return uri;
}

/**
 * The mark to show: the stored one, else one drawn from the logo. `null`
 * while it draws, and when the logo cannot be read.
 */
export function useLogoMark(logoUri: string | null | undefined, storedUri?: string | null): string | null {
  const [derived, setDerived] = useState<string | null>(() =>
    logoUri ? (cache.get(keyOf(logoUri)) ?? null) : null,
  );
  useEffect(() => {
    if (storedUri || !logoUri) return;
    let live = true;
    void deriveMark(logoUri).then((uri) => {
      if (live) setDerived(uri);
    });
    return () => {
      live = false;
    };
  }, [logoUri, storedUri]);
  return storedUri ?? derived;
}
