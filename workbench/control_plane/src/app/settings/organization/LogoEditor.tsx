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
 *
 * ⚠️ The logo for a LIGHT background is the one the admin always gives. The
 * dark-mode version is optional: the editor can make one (white version, light
 * card), or the admin uploads the version they already have for a dark
 * background ("My dark version", owner request 2026-10-09). That second file
 * is trimmed to the logo by itself and gets no crop of its own.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BrandMark } from "@/components/OrgBrandLockup";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import Modal from "@/components/ui/Modal";
import { LOGO_PICK_ACCEPT, type Rendered, type Source, decodeFile, renderLogo } from "@/lib/logoCanvas";
import {
  type Background,
  type Box,
  type DarkStyle,
  adviseDarkStyle,
  contentBounds,
  detectBackground,
  inkStats,
} from "@/lib/logoImage";
import { type OrgBranding, type OrgLogo, precheckLogoFile } from "@/lib/orgBranding";
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
  { id: "own", label: "My dark version", note: "Upload the logo you use on dark backgrounds." },
];

/** The admin's own dark-background file, decoded. */
export interface OwnDark {
  source: Source;
  name: string;
  /** The saved bytes, when the editor reopens a saved own version. */
  storedBase64?: string;
}

/**
 * What Save sends, or `null` while it cannot send yet.
 *
 * ⚠️ A reopened logo ("Change dark mode") sends its SAVED bytes. The editor
 * must not run the trim or the background removal on it again: a logo saved
 * with its background kept would lose it with no warning (review,
 * 2026-10-09). A saved own version is sent as saved too, unless the admin
 * changes it.
 */
export function savePayload(p: {
  main: Rendered | null;
  savedMain: string | null;
  chosen: DarkStyle;
  white: Rendered | null;
  own: OwnDark | null;
  ownRendered: Rendered | null;
  ownRemoveBg: boolean;
}): LogoSave | null {
  const logoBase64 = p.savedMain ?? p.main?.base64 ?? null;
  if (!logoBase64) return null;
  let dark: string | null = null;
  if (p.chosen === "white") dark = p.white?.base64 ?? null;
  if (p.chosen === "own" && p.own) {
    dark = p.own.storedBase64 && !p.ownRemoveBg ? p.own.storedBase64 : (p.ownRendered?.base64 ?? null);
  }
  if ((p.chosen === "white" || p.chosen === "own") && !dark) return null;
  return { logoBase64, logoDarkBase64: dark, darkStyle: p.chosen };
}

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
  initialStyle = null,
  initialOwn = null,
  savedMain = null,
}: {
  source: Source;
  fileName: string;
  saving: boolean;
  error: string;
  onCancel: () => void;
  onSave: (save: LogoSave) => void;
  /** The stored choice, when the editor reopens the current logo. */
  initialStyle?: DarkStyle | null;
  initialOwn?: OwnDark | null;
  /** The saved logo's bytes, when the editor reopens it. Sent back unchanged. */
  savedMain?: string | null;
}) {
  const reopened = savedMain !== null;
  const bg: Background = useMemo(() => detectBackground(source.pixels), [source]);
  const fit: Box = useMemo(() => contentBounds(source.pixels, bg), [source, bg]);
  const whole: Box = useMemo(() => ({ x: 0, y: 0, w: source.pixels.width, h: source.pixels.height }), [source]);
  // A reopened logo is drawn as saved: the whole image, nothing removed.
  const [box, setBox] = useState<Box>(reopened ? whole : fit);
  const [removeBg, setRemoveBg] = useState(!reopened && bg.kind === "solid");
  const solidRgb = bg.kind === "solid" ? bg.rgb : null;

  const [main, setMain] = useState<Rendered | null>(null);
  const [white, setWhite] = useState<Rendered | null>(null);
  const advice: DarkStyle = useMemo(() => {
    if (!main) return "same";
    return adviseDarkStyle(inkStats(main.pixels));
  }, [main]);
  const [style, setStyle] = useState<DarkStyle | null>(initialStyle);
  const chosen = style ?? advice;

  // ── The admin's own dark-background version ──────────────────────────────
  const [own, setOwn] = useState<OwnDark | null>(initialOwn);
  const [ownError, setOwnError] = useState("");
  const ownInput = useRef<HTMLInputElement>(null);
  const ownBg: Background | null = useMemo(() => (own ? detectBackground(own.source.pixels) : null), [own]);
  // A dark version usually sits on black. Clear it by default, as the light
  // one, but never on a saved version, which is drawn as it was saved.
  const [ownRemoveBg, setOwnRemoveBg] = useState(!initialOwn?.storedBase64);
  const ownSolid = ownBg?.kind === "solid" ? ownBg.rgb : null;
  const [ownRendered, setOwnRendered] = useState<Rendered | null>(null);
  useEffect(() => {
    if (!own || !ownBg) return;
    const id = requestAnimationFrame(() => {
      const area = own.storedBase64
        ? { x: 0, y: 0, w: own.source.pixels.width, h: own.source.pixels.height }
        : contentBounds(own.source.pixels, ownBg);
      setOwnRendered(renderLogo(own.source, area, { removeBackground: ownRemoveBg ? ownSolid : null }));
    });
    return () => cancelAnimationFrame(id);
  }, [own, ownBg, ownRemoveBg, ownSolid]);

  const pickOwn = async (file: File | undefined) => {
    if (ownInput.current) ownInput.current.value = "";
    if (!file) return;
    const complaint = precheckLogoFile(file);
    if (complaint) {
      setOwnError(complaint);
      return;
    }
    try {
      const decoded = await decodeFile(file);
      setOwnError("");
      setOwnRemoveBg(true);
      // A new file: no saved bytes, so it is trimmed and drawn.
      setOwn({ source: decoded, name: file.name });
      setStyle("own");
    } catch (e) {
      setOwnError(e instanceof Error ? e.message : "That file could not be opened.");
    }
  };
  /** "My dark version" with no file yet opens the picker, and selects on a pick. */
  const choose = (id: DarkStyle) => {
    setOwnError("");
    if (id === "own" && !own) ownInput.current?.click();
    else setStyle(id);
  };
  const darkImage: Rendered | null = chosen === "white" ? white : chosen === "own" && own ? ownRendered : null;
  const payload = savePayload({ main, savedMain, chosen, white, own, ownRendered, ownRemoveBg });

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
        logoDark: darkImage ? asLogo(darkImage) : null,
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
      description={
        reopened
          ? "Choose how your saved logo shows in dark mode."
          : `From ${fileName}. Crop it, then check it in light and dark mode.`
      }
      icon="Image"
      size="2xl"
      placement="top"
    >
      <div className="flex max-h-[75vh] flex-col gap-4 overflow-y-auto p-4">
        {reopened ? (
          <p className="text-xs text-muted-foreground">
            Your saved logo stays as it is. To change it, choose Replace logo.
          </p>
        ) : null}
        {/* The image, on a checkerboard so a clear background shows as clear. */}
        <div className={`flex flex-col items-center gap-2 ${reopened ? "hidden" : ""}`}>
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
          <input
            ref={ownInput}
            type="file"
            accept={LOGO_PICK_ACCEPT}
            className="hidden"
            data-testid="logo-own-dark-input"
            onChange={(e) => void pickOwn(e.target.files?.[0])}
          />
          <div role="radiogroup" aria-labelledby="logo-dark-mode" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {STYLES.map((s) => (
              <Button
                key={s.id}
                variant="secondary"
                size="none"
                layout="flex flex-col items-start"
                role="radio"
                aria-checked={chosen === s.id}
                selected={chosen === s.id}
                onClick={() => choose(s.id)}
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
          {chosen === "own" && own ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span className="max-w-[16rem] truncate">From {own.name}, trimmed to the logo.</span>
              <Button variant="secondary" size="sm" icon="Upload" onClick={() => ownInput.current?.click()}>
                Choose another file
              </Button>
              {ownSolid ? (
                <label className="flex items-center gap-2 text-sm text-foreground">
                  <Checkbox checked={ownRemoveBg} onChange={(e) => setOwnRemoveBg(e.target.checked)} />
                  Remove the background behind it
                </label>
              ) : null}
            </div>
          ) : null}
          {ownError ? <p className="text-xs text-destructive">{ownError}</p> : null}
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
            disabled={!payload || saving}
            onClick={() => payload && onSave(payload)}
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
