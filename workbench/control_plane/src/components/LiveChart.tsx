"use client";

/**
 * A live chart in the web chat (WS-47 WAC-10g).
 *
 * It draws the SAME short spec the WhatsApp bot sends as an image, through
 * the same `lib/charts/kinds.mjs`, so a chart reads the same on both. Here it
 * is live: a tooltip on hover, a legend that hides a series, a resize with
 * the chat column, and the member's colour mode.
 *
 * A bad spec shows its reason in place of the chart, never a blank card.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMode } from "@/lib/theme/surfaces";
import { resolveHue } from "@/lib/statusAccent";
import { TYPE } from "@/lib/typeScale";
import { ChartSpecError, chartOption } from "@/lib/charts/kinds.mjs";

type Spec = Record<string, unknown>;
type Built = { option: unknown; height: number } | { error: string };

/** The font the page uses: next/font names Geist with its own family. */
function pageFont(): string | undefined {
  if (typeof document === "undefined") return undefined;
  return getComputedStyle(document.body).fontFamily || undefined;
}

/** A status word or colour as one of statusAccent's six hues. */
const hueOf = (w: unknown) => (typeof w === "string" ? resolveHue({ color: w, category: w, name: w }) : w);

/**
 * The spec with each status word as a hue NAME. The chart engine knows the
 * six names only, and the rule from a word ("On hold") to a hue is
 * statusAccent's, the product's one colour vocabulary (WAC-10f review).
 */
export function withHues(spec: Spec): Spec {
  const out: Spec = { ...spec };
  if (Array.isArray(spec.tones)) out.tones = spec.tones.map(hueOf);
  if (Array.isArray(spec.rows)) {
    out.rows = spec.rows.map((r) =>
      r && typeof r === "object" && (r as Spec).status != null ? { ...(r as Spec), status: hueOf((r as Spec).status) } : r);
  }
  return out;
}

/**
 * The most the web chat scales the chart language's 1080-unit drawing. The
 * engine sizes its type for a 1080-pixel image, so a scale that follows a
 * 768-pixel chat column drew axis labels near 17 px and a 28 px title,
 * twice the text beside them (report visual review, 2026-10-16). Past
 * 1080 x WEB_MAX_SCALE pixels the drawing grows WIDER, at the same type
 * size: axis labels near 11 px, a legend near 12 px. A narrower box still
 * scales down, as before.
 */
export const WEB_MAX_SCALE = 0.45;

/** The scale of a chart drawn `width` pixels wide in the web chat. */
export function webScale(width: number): number {
  return Math.min(width / 1080, WEB_MAX_SCALE);
}

/**
 * `maxHeight` caps the drawing's height, for a chart inside a card: the
 * engine lays its plot out from the height it gets, so a capped chart is
 * shorter, never cut.
 */
export function buildChart(spec: Spec, width: number, mode: "dark" | "light", font?: string, maxHeight?: number): Built {
  try {
    const { option, height } = chartOption(withHues(spec), {
      width, scale: webScale(width), mode, font, footer: false, animate: true, interactive: true,
    });
    return { option, height: maxHeight ? Math.min(height, maxHeight) : height };
  } catch (err) {
    return { error: err instanceof ChartSpecError ? err.message : "This chart could not be drawn." };
  }
}

/**
 * `bare`: no frame of its own. The chart sits in a chat card's frame, under
 * the card's header (`ChartCard` in `genUITemplates.tsx`), and a second
 * border there drew a card in a card.
 */
export default function LiveChart({ spec, bare = false, maxHeight }: { spec: Spec; bare?: boolean; maxHeight?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const mode = useMode();
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Keyed by the spec's CONTENT. A streamed token renders the message again,
  // and a template may hand a fresh but equal object each time; a key by
  // identity redrew the chart from zero on every token (WAC-10g review).
  const specKey = JSON.stringify(spec);
  const built = useMemo(
    () => (width > 0 ? buildChart(JSON.parse(specKey) as Spec, width, mode, pageFont(), maxHeight) : null),
    [specKey, width, mode, maxHeight],
  );

  useEffect(() => {
    const el = box.current?.firstElementChild as HTMLDivElement | null;
    if (!el || !built || "error" in built) return;
    let alive = true;
    let chart: { dispose(): void; setOption(o: unknown): void } | null = null;
    void import("@/lib/charts/echarts").then(({ echarts }) => {
      if (!alive) return;
      const c = echarts.init(el, null, { renderer: "svg", width, height: built.height });
      c.setOption(built.option as never);
      chart = c;
    });
    return () => {
      alive = false;
      chart?.dispose();
    };
  }, [built, width]);

  return (
    <div
      ref={box}
      data-chart-max-height={maxHeight}
      style={
        bare
          ? { width: "100%", overflow: "hidden" }
          : { width: "100%", borderRadius: 12, border: "1px solid var(--border)", overflow: "hidden", background: "var(--card)" }
      }
    >
      {built && "error" in built ? (
        <div role="note" style={{ padding: 14, fontSize: TYPE.sm, color: "var(--muted-foreground)" }}>
          {built.error}
        </div>
      ) : (
        <div style={{ width: "100%", height: built ? built.height : Math.min(220, maxHeight ?? 220) }} />
      )}
    </div>
  );
}
