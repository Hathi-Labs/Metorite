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

export function buildChart(spec: Spec, width: number, mode: "dark" | "light", font?: string): Built {
  try {
    const { option, height } = chartOption(withHues(spec), { width, mode, font, footer: false, animate: true, interactive: true });
    return { option, height };
  } catch (err) {
    return { error: err instanceof ChartSpecError ? err.message : "This chart could not be drawn." };
  }
}

export default function LiveChart({ spec }: { spec: Spec }) {
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
    () => (width > 0 ? buildChart(JSON.parse(specKey) as Spec, width, mode, pageFont()) : null),
    [specKey, width, mode],
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
      style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)", overflow: "hidden", background: "var(--card)" }}
    >
      {built && "error" in built ? (
        <div role="note" style={{ padding: 14, fontSize: TYPE.sm, color: "var(--muted-foreground)" }}>
          {built.error}
        </div>
      ) : (
        <div style={{ width: "100%", height: built ? built.height : 220 }} />
      )}
    </div>
  );
}
