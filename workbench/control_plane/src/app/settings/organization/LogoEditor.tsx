"use client";

/**
 * The logo editor (owner request, 2026-10-09): pick any image, crop it, and
 * see it in the sidebar in light AND dark mode before it is saved.
 *
 * What it replaces. An admin picked a file and met one of six refusals, and
 * then the save itself failed. Now the editor opens almost any image (PNG,
 * JPEG, WebP, GIF, SVG, any size), trims the empty margin, and draws the small
 * PNG the shell needs (`logoCanvas.ts`). The admin adjusts three things at
 * most: the crop, the background, and how dark mode shows the logo.
 *
 * The decisions are `logoImage.ts`'s, tested without a browser. This file is
 * the controls and the previews.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BrandMark } from "@/components/OrgBrandLockup";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import Modal from "@/components/ui/Modal";
import { type Rendered, type Source, renderLogo } from "@/lib/logoCanvas";
import {
  type Background,
  type Box,
  type DarkStyle,
  adviseDarkStyle,
  contentBounds,
  detectBackground,
  inkStats,
} from "@/lib/logoImage";
import type { OrgBranding, OrgLogo } from "@/lib/orgBranding";
import { THEME } from "@/lib/theme/themes";

export interface LogoSave {
  logoBase64: string;
  logoDarkBase64: string | null;
  darkStyle: DarkStyle;
}

const STAGE_W = 520;
const STAGE_H = 240;
const MIN_CROP = 8;

const STYLES: { id: DarkStyle; label: string; note: string }[] = [
  { id: "same", label: "As it is", note: "The logo reads on the dark sidebar." },
  { id: "white", label: "White version", note: "Black or grey parts turn white. Colours stay." },
  { id: "plate", label: "On a light card", note: "Keeps a dark, colourful logo's colours." },
];

const asLogo = (r: Rendered): OrgLogo => ({
  dataUri: r.dataUri,
  mime: "image/png",
  width: r.width,
  height: r.height,
  byteSize: r.bytes,
});

/** One colour mode's sidebar, from the theme mirror, so both show side by side. */
export function modeVars(mode: "light" | "dark"): React.CSSProperties {
  const c = THEME.colors[mode];
  return {
    background: c.sidebarBackground,
    borderColor: c.sidebarBorder,
    ["--muted-foreground" as string]: c.mutedForeground,
    ["--sidebar-foreground" as string]: c.sidebarForeground,
  };
}

export default function LogoEditor({
  source,
  fileName,
  saving,
  error,
  onCancel,
  onSave,
}: {
  source: Source;
  fileName: string;
  saving: boolean;
  error: string;
  onCancel: () => void;
  onSave: (save: LogoSave) => void;
}) {
  const bg: Background = useMemo(() => detectBackground(source.pixels), [source]);
  const fit: Box = useMemo(() => contentBounds(source.pixels, bg), [source, bg]);
  const [box, setBox] = useState<Box>(fit);
  const [removeBg, setRemoveBg] = useState(bg.kind === "solid");
  const solidRgb = bg.kind === "solid" ? bg.rgb : null;

  const [main, setMain] = useState<Rendered | null>(null);
  const [white, setWhite] = useState<Rendered | null>(null);
  const advice: DarkStyle = useMemo(() => {
    if (!main) return "same";
    return adviseDarkStyle(inkStats(main.pixels));
  }, [main]);
  const [style, setStyle] = useState<DarkStyle | null>(null);
  const chosen = style ?? advice;

  // Redraw on every change, one frame later, so a drag stays smooth.
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const opts = { removeBackground: removeBg ? solidRgb : null };
      setMain(renderLogo(source, box, opts));
      setWhite(renderLogo(source, box, { ...opts, white: true }));
    });
    return () => cancelAnimationFrame(id);
  }, [source, box, removeBg, solidRgb]);

  const preview: OrgBranding | null = main
    ? {
        logo: asLogo(main),
        logoDark: chosen === "white" && white ? asLogo(white) : null,
        darkStyle: chosen,
        updatedBy: "",
        updatedAt: "",
      }
    : null;

  // ── The crop stage ────────────────────────────────────────────────────────
  const { width: W, height: H } = source.pixels;
  // Encoded once: a 2048px PNG per redraw would make every drag stutter.
  const stageSrc = useMemo(() => source.canvas.toDataURL(), [source]);
  const scale = Math.min(STAGE_W / W, STAGE_H / H, 4);
  const drag = useRef<{ kind: string; x: number; y: number; start: Box } | null>(null);

  // One handler for the box and its four corners: `data-kind` says which.
  const onDown = (e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    const kind = e.currentTarget.dataset.kind ?? "move";
    drag.current = { kind, x: e.clientX, y: e.clientY, start: box };
  };
  const onMove = useCallback(
    (e: React.PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      const dx = (e.clientX - d.x) / scale;
      const dy = (e.clientY - d.y) / scale;
      setBox(moveBox(d.start, d.kind, dx, dy, W, H));
    },
    [scale, W, H],
  );
  const onUp = () => {
    drag.current = null;
  };

  const handle = (kind: string, pos: string) => (
    <span
      key={kind}
      role="presentation"
      data-kind={kind}
      onPointerDown={onDown}
      className={`absolute h-3 w-3 rounded-sm border border-background bg-primary ${pos}`}
    />
  );

  return (
    <Modal
      open
      onClose={onCancel}
      title="Your logo"
      description={`From ${fileName}. Crop it, then check it in light and dark mode.`}
      icon="Image"
      size="2xl"
      placement="top"
    >
      <div className="flex max-h-[75vh] flex-col gap-4 overflow-y-auto p-4">
        {/* The image, on a checkerboard so a clear background shows as clear. */}
        <div className="flex flex-col items-center gap-2">
          <div
            className="relative select-none overflow-hidden rounded-md border border-border bg-[conic-gradient(var(--muted)_25%,var(--background)_0_50%,var(--muted)_0_75%,var(--background)_0)] bg-[length:16px_16px]"
            style={{ width: W * scale, height: H * scale, touchAction: "none" }}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={onUp}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={stageSrc}
              alt=""
              draggable={false}
              className="pointer-events-none absolute inset-0 h-full w-full"
            />
            <div
              data-testid="logo-crop"
              aria-label="Crop area. Drag to move it, or drag a corner to resize it."
              data-kind="move"
              onPointerDown={onDown}
              className="absolute cursor-move border-2 border-primary shadow-[0_0_0_9999px_color-mix(in_srgb,var(--background)_65%,transparent)]"
              style={{ left: box.x * scale, top: box.y * scale, width: box.w * scale, height: box.h * scale }}
            >
              {handle("nw", "-left-1.5 -top-1.5 cursor-nwse-resize")}
              {handle("ne", "-right-1.5 -top-1.5 cursor-nesw-resize")}
              {handle("sw", "-bottom-1.5 -left-1.5 cursor-nesw-resize")}
              {handle("se", "-bottom-1.5 -right-1.5 cursor-nwse-resize")}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button variant="secondary" size="sm" icon="Scan" onClick={() => setBox(fit)}>
              Fit to the logo
            </Button>
            <Button variant="secondary" size="sm" icon="Maximize" onClick={() => setBox({ x: 0, y: 0, w: W, h: H })}>
              Whole image
            </Button>
          </div>
          {solidRgb && (
            <label className="flex items-center gap-2 text-sm text-foreground">
              <Checkbox checked={removeBg} onChange={(e) => setRemoveBg(e.target.checked)} />
              Remove the background behind the logo
            </label>
          )}
        </div>

        {/* Dark mode: one logo, and how it shows on the dark sidebar. */}
        <section aria-labelledby="logo-dark-mode" className="flex flex-col gap-2">
          <h3 id="logo-dark-mode" className="text-sm font-semibold text-foreground">
            In dark mode
          </h3>
          <div role="radiogroup" aria-labelledby="logo-dark-mode" className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {STYLES.map((s) => (
              <Button
                key={s.id}
                variant="secondary"
                size="none"
                layout="flex flex-col items-start"
                role="radio"
                aria-checked={chosen === s.id}
                selected={chosen === s.id}
                onClick={() => setStyle(s.id)}
                className="gap-0.5 px-3 py-2 text-left"
              >
                <span className="text-sm font-medium text-foreground">
                  {s.label}
                  {advice === s.id ? <span className="ml-1.5 text-xs font-normal text-primary">Suggested</span> : null}
                </span>
                <span className="text-xs text-muted-foreground">{s.note}</span>
              </Button>
            ))}
          </div>
        </section>

        {/* The sidebar's top-left, in both modes, from the shell's own mark. */}
        <section aria-label="Preview" className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {(["light", "dark"] as const).map((mode) => (
            <div key={mode} className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">{mode === "light" ? "Light mode" : "Dark mode"}</span>
              <div
                data-testid={`logo-preview-${mode}`}
                className="flex h-16 w-[184px] items-center rounded-lg border px-4"
                style={modeVars(mode)}
              >
                {preview ? <BrandMark branding={preview} fallbackCaption="" mode={mode} maxWidth={152} /> : null}
              </div>
            </div>
          ))}
        </section>

        {error ? (
          <p className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">{error}</p>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
          <Button
            size="sm"
            icon="Check"
            disabled={!main || saving}
            onClick={() =>
              main &&
              onSave({
                logoBase64: main.base64,
                logoDarkBase64: chosen === "white" && white ? white.base64 : null,
                darkStyle: chosen,
              })
            }
          >
            {saving ? "Saving…" : "Save logo"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** Move or resize the crop box by a drag, kept inside the image. */
export function moveBox(start: Box, kind: string, dx: number, dy: number, W: number, H: number): Box {
  let { x, y, w, h } = start;
  if (kind === "move") {
    x = clamp(x + dx, 0, W - w);
    y = clamp(y + dy, 0, H - h);
    return { x, y, w, h };
  }
  if (kind.includes("w")) {
    const nx = clamp(x + dx, 0, x + w - MIN_CROP);
    w += x - nx;
    x = nx;
  }
  if (kind.includes("e")) w = clamp(w + dx, MIN_CROP, W - x);
  if (kind.includes("n")) {
    const ny = clamp(y + dy, 0, y + h - MIN_CROP);
    h += y - ny;
    y = ny;
  }
  if (kind.includes("s")) h = clamp(h + dy, MIN_CROP, H - y);
  return { x, y, w, h };
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
