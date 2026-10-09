/**
 * The logo pipeline's browser half: decode any image the admin has, and draw
 * the PNG the shell stores. The decisions live in `logoImage.ts`, which is
 * tested without a browser.
 *
 * ⚠️ An SVG is rasterised HERE, in the uploader's own browser, through an
 * `<img>`. In that context an SVG runs no script and loads nothing external,
 * so drawing it is safe, and the server only ever receives PNG bytes. The
 * server still refuses SVG bytes outright (`gateway/image_probe.py`).
 */
import {
  type Box,
  type Pixels,
  fitAspect,
  outputSize,
  removeSolid,
  toWhite,
} from "@/lib/logoImage";

/** Files the picker offers. SVG is accepted because it becomes a PNG here. */
export const LOGO_PICK_ACCEPT = "image/png,image/jpeg,image/webp,image/gif,image/svg+xml";

/** The decoded source, at most 2048px on its longer side. */
export interface Source {
  canvas: HTMLCanvasElement;
  pixels: Pixels;
}

/** Below the server's 128 KB ceiling, with room for the JSON around it. */
const MAX_OUTPUT_BYTES = 120 * 1024;

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("That file could not be opened as an image."));
    img.src = url;
  });
}

/** Decode a picked file. An SVG with no size of its own is drawn 1200px wide. */
export async function decodeFile(file: File): Promise<Source> {
  const url = URL.createObjectURL(file);
  try {
    const img = await loadImage(url);
    let w = img.naturalWidth || 1200;
    let h = img.naturalHeight || Math.round(w / 3);
    const longest = Math.max(w, h);
    if (longest > 2048) {
      w = Math.round((w * 2048) / longest);
      h = Math.round((h * 2048) / longest);
    } else if (file.type === "image/svg+xml" && longest < 1200) {
      // Vector art scales for free, so draw it big enough to stay sharp.
      w = Math.round((w * 1200) / longest);
      h = Math.round((h * 1200) / longest);
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, w);
    canvas.height = Math.max(1, h);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("This browser cannot edit images.");
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
    return { canvas, pixels: { data: data.data, width: data.width, height: data.height } };
  } finally {
    URL.revokeObjectURL(url);
  }
}

export interface Rendered {
  /** Base64 of the PNG, without the `data:` prefix. */
  base64: string;
  dataUri: string;
  width: number;
  height: number;
  bytes: number;
  /** The output's own pixels, for the dark-mode advice (`inkStats`). */
  pixels: Pixels;
}

export interface RenderOptions {
  /** Clear this solid background colour first. */
  removeBackground?: readonly number[] | null;
  /** Every visible pixel white, for a dark-mode version. */
  white?: boolean;
}

/**
 * Draw `box` of the source as the stored PNG: its shape fitted to the
 * server's bounds, sized to the slot at 3×, then 2× if 3× is too heavy.
 */
export function renderLogo(source: Source, box: Box, opts: RenderOptions = {}): Rendered {
  const fitted = fitAspect(box);
  for (const density of [3, 2]) {
    const size = outputSize(fitted, density);
    const out = document.createElement("canvas");
    out.width = size.width;
    out.height = size.height;
    const ctx = out.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("This browser cannot edit images.");
    ctx.imageSmoothingQuality = "high";
    // The fitted box can reach past the image; that area stays transparent.
    const sx = size.width / fitted.w;
    const sy = size.height / fitted.h;
    ctx.drawImage(
      source.canvas,
      (0 - fitted.x) * sx,
      (0 - fitted.y) * sy,
      source.canvas.width * sx,
      source.canvas.height * sy,
    );
    const img = ctx.getImageData(0, 0, size.width, size.height);
    const px: Pixels = { data: img.data, width: img.width, height: img.height };
    if (opts.removeBackground || opts.white) {
      if (opts.removeBackground) removeSolid(px, opts.removeBackground);
      if (opts.white) toWhite(px);
      ctx.putImageData(img, 0, 0);
    }
    const dataUri = out.toDataURL("image/png");
    const base64 = dataUri.slice(dataUri.indexOf(",") + 1);
    const bytes = Math.floor((base64.length * 3) / 4);
    if (bytes <= MAX_OUTPUT_BYTES || density === 2) {
      return { base64, dataUri, width: size.width, height: size.height, bytes, pixels: px };
    }
  }
  throw new Error("unreachable");
}
