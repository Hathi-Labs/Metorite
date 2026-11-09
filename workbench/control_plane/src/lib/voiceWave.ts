/**
 * The picture of a recording (`projects_ai_chat.md` §31, VOX-7).
 *
 * Three soft sine lines swell with the voice. This file holds the pure
 * parts: the path of each line, the smoothing of the level, the static meter
 * for reduced motion, the "N s left" rule and the first-recording hint. The
 * vitest rig has no DOM, so a rule that lives only in JSX has no test.
 * `components/VoiceWave.tsx` draws the result.
 *
 * Colour is not here. Each line strokes `currentColor`, and the wave sits in
 * a `text-primary` box. A changed accent therefore moves all three lines.
 */
import { MAX_SECONDS } from "@/lib/voiceDictation";

/** The viewBox of the wave. The SVG stretches it to the box it sits in. */
export const WAVE_W = 100;
export const WAVE_H = 32;

export interface WaveLayer {
  /** Cycles across the width. */
  freq: number;
  /** Phase speed. A sign sets the direction. */
  speed: number;
  /** Share of the full swing, 0..1. */
  amp: number;
  /** Stroke width in px. */
  weight: number;
  /** Stroke opacity of the line. */
  opacity: number;
}

/** Back to front: a faint wide line, a middle line, a strong front line. */
export const WAVE_LAYERS: readonly WaveLayer[] = [
  { freq: 1.6, speed: 1.3, amp: 0.55, weight: 1.5, opacity: 0.35 },
  { freq: 2.2, speed: -1.7, amp: 0.75, weight: 1.75, opacity: 0.6 },
  { freq: 3.0, speed: 2.4, amp: 1, weight: 2.25, opacity: 1 },
];

/** The ring around the stop button, as a scale and an opacity. */
export function ringOf(level: number): { scale: number; opacity: number } {
  const v = clamp01(level);
  return { scale: 0.92 + v * 0.34, opacity: 0.3 + v * 0.7 };
}

export function clamp01(n: number): number {
  return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0;
}

/** One step of the level shown: it rises fast and falls slowly. */
export function smoothLevel(shown: number, raw: number): number {
  const r = clamp01(raw);
  return shown + (r - shown) * (r > shown ? 0.55 : 0.12);
}

/** The `d` of one line at time `t` (seconds) and level `level` (0..1). */
export function wavePath(layer: WaveLayer, t: number, level: number): string {
  const mid = WAVE_H / 2;
  const swing = (0.08 + 0.92 * Math.pow(clamp01(level), 0.85)) * (mid - 2);
  let d = "";
  for (let x = 0; x <= WAVE_W; x += 2) {
    const u = x / WAVE_W;
    const envelope = Math.pow(Math.sin(Math.PI * u), 1.6);
    const y = mid + Math.sin(u * Math.PI * 2 * layer.freq + t * layer.speed * 2.2) * swing * layer.amp * envelope;
    d += `${x === 0 ? "M" : "L"}${x},${y.toFixed(2)}`;
  }
  return d;
}

/** How many of `n` meter segments are lit at this level (reduced motion). */
export function litSegments(level: number, n: number): number {
  return Math.round(clamp01(level) * n);
}

/** The last seconds in which the timer warns. */
export const WARN_SECONDS = 15;

/** Whole seconds left when the recording is within the warning window, else null. */
export function secondsLeft(elapsed: number): number | null {
  const left = MAX_SECONDS - elapsed;
  return left <= WARN_SECONDS ? Math.max(0, left) : null;
}

// ── The first-recording hint ────────────────────────────────────────────────

export const HINT_KEY = "metorite.voice.stop-hint-seen";
export const HINT_TEXT = "Tap ■ to finish";
/** The hint fades after this many milliseconds. */
export const HINT_MS = 4500;

type KeyStore = Pick<Storage, "getItem" | "setItem">;

function defaultStore(): KeyStore | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** Has the member seen the hint? A blocked store reads as "not seen". */
export function hintSeen(store: KeyStore | null = defaultStore()): boolean {
  try {
    return store?.getItem(HINT_KEY) === "1";
  } catch {
    return false;
  }
}

export function markHintSeen(store: KeyStore | null = defaultStore()): void {
  try {
    store?.setItem(HINT_KEY, "1");
  } catch {
    // A blocked store shows the hint again next time. That harms nothing.
  }
}
