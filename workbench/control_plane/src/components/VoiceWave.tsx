"use client";

/**
 * The picture of a recording: three layered sine lines (VOX-7,
 * `projects_ai_chat.md` §31). The lines stroke `currentColor` in a
 * `text-primary` box, so the accent moves them. The level comes in a ref and
 * a `requestAnimationFrame` loop draws it, so a block of audio costs no
 * React render. The loop starts on mount, stops on unmount, and writes the
 * ring of the stop button as well.
 *
 * With reduced motion, the lines do not move. A calm meter of segments
 * shows the level, and it updates about eight times a second.
 */
import { useEffect, useRef, useSyncExternalStore } from "react";

import { litSegments, ringOf, smoothLevel, WAVE_H, WAVE_LAYERS, WAVE_W, wavePath } from "@/lib/voiceWave";

const SEGMENTS = 10;
const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void): () => void {
  if (typeof matchMedia !== "function") return () => {};
  const q = matchMedia(REDUCED_QUERY);
  q.addEventListener("change", onChange);
  return () => q.removeEventListener("change", onChange);
}

/** Does the member ask for less motion? Follows a change while mounted. */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => (typeof matchMedia === "function" ? matchMedia(REDUCED_QUERY).matches : false),
    () => false,
  );
}

export default function VoiceWave({
  levelRef,
  reduced,
  ringRef,
  className = "",
}: {
  levelRef: { current: number };
  reduced: boolean;
  /** The ring round the stop button. The loop sets its scale and opacity. */
  ringRef: { current: HTMLElement | null };
  className?: string;
}) {
  const paths = useRef<(SVGPathElement | null)[]>([]);
  const segs = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    let shown = 0;
    const setRing = (level: number) => {
      const el = ringRef.current;
      if (!el) return;
      const r = ringOf(level);
      el.style.transform = `scale(${r.scale.toFixed(3)})`;
      el.style.opacity = r.opacity.toFixed(3);
    };

    if (reduced) {
      // A calm meter. No frame loop: a timer, at about eight ticks a second.
      const ring = ringRef.current;
      if (ring) {
        ring.style.transform = "none";
        ring.style.opacity = "0.55";
      }
      const id = setInterval(() => {
        shown = smoothLevel(shown, levelRef.current);
        const on = litSegments(shown, SEGMENTS);
        segs.current.forEach((s, i) => s?.setAttribute("data-on", i < on ? "true" : "false"));
      }, 120);
      return () => clearInterval(id);
    }

    const t0 = performance.now();
    let frame = 0;
    const draw = (now: number) => {
      shown = smoothLevel(shown, levelRef.current);
      const t = (now - t0) / 1000;
      WAVE_LAYERS.forEach((layer, i) => paths.current[i]?.setAttribute("d", wavePath(layer, t, shown)));
      setRing(shown);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [reduced, levelRef, ringRef]);

  if (reduced) {
    return (
      <span aria-hidden="true" data-chat-voice-wave="meter" className={`flex h-8 min-w-8 max-w-28 flex-1 items-center gap-[3px] overflow-hidden text-primary ${className}`}>
        {Array.from({ length: SEGMENTS }, (_, i) => (
          <span
            key={i}
            ref={(el) => {
              segs.current[i] = el;
            }}
            data-on="false"
            className="h-3.5 min-w-px max-w-1 flex-1 rounded-sm bg-muted data-[on=true]:bg-primary"
          />
        ))}
      </span>
    );
  }

  return (
    // The SVG fills a box with no size of its own, so the box can shrink to
    // 2 rem on a narrow bar and the cluster never forces a wrap.
    <span className={`relative h-8 min-w-8 flex-1 text-primary ${className}`}>
      <svg
        aria-hidden="true"
        data-chat-voice-wave="lines"
        viewBox={`0 0 ${WAVE_W} ${WAVE_H}`}
        preserveAspectRatio="none"
        className="absolute inset-0 h-full w-full"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {WAVE_LAYERS.map((layer, i) => (
          <path
            key={i}
            ref={(el) => {
              paths.current[i] = el;
            }}
            d={wavePath(layer, 0, 0)}
            strokeWidth={layer.weight}
            strokeOpacity={layer.opacity}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
    </span>
  );
}
