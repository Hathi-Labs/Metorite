/**
 * Projects · one mark family for every run state.
 *
 * Owner directive, 2026-09-23: *"when I'm changing the state from pause,
 * stopped, etc. The iconography does not have the same weight as the progress
 * circle... generate new icons for the other states of the project so that we
 * have a continuity of iconography and everything looks like it has the same
 * weight."* And, about the wheel itself: *"increase the contrast a little bit
 * more... or the thickness"*.
 *
 * ## One ring, five insides
 *
 * Every state draws the SAME ring — one radius, one stroke — and differs only
 * in what it does with it. The five are still told apart by SHAPE as well as
 * hue, which is what D-PM-27 asks for:
 *
 *   • queued  — a DASHED ring. Nothing has started, so the ring is not whole.
 *   • active  — a faint track and a progress ARC (the completion wheel).
 *   • on_hold — a whole ring and two BARS.
 *   • stopped — a whole ring and a SQUARE.
 *   • done    — a whole ring and a CHECK.
 *
 * Before this, `active` was a heavy custom ring and the other four were Lucide
 * glyphs at Lucide's thin stroke. In one column they read as two icon sets.
 *
 * ## Why the weight is what it is
 *
 * Chosen by rendering three weights at the true sizes, in dark and light, and
 * looking (2026-09-23). A stroke of 3.5 turned Paused and Stopped into near-
 * solid discs at 14px — the ring leaves too small a hole for a mark. A stroke
 * of 3 at 16px keeps all five apart. The wheel gains about a third in screen
 * thickness over the old 2.5 at 14px. See `TRACK_OPACITY` for the track.
 *
 * ## Why this is data and not JSX
 *
 * `vitest.config.ts` is `environment: "node"` and never collects a `.tsx`
 * (D-PM-21). A rule that lives in a component has no fence. So the shapes are
 * described here as plain numbers, `StateMark.tsx` draws exactly what these
 * return, and the tests measure them: nothing clips its box, no inner mark
 * touches the ring, and no two states share a shape.
 */

import { completion, ringDash, type NodeProgress } from "./progressWheel";

/** The viewBox is `0 0 MARK_BOX MARK_BOX`. The centre is at half of it. */
export const MARK_BOX = 16;
export const MARK_CENTER = MARK_BOX / 2;
/** One radius and one stroke for every state. The whole point of the family. */
export const MARK_RADIUS = 6;
export const MARK_STROKE = 3;
/**
 * The wheel's track. Fainter than the arc, because the ARC is the information.
 *
 * ⚠️ Not fainter than this. 0.18 was tried first and read well in dark mode,
 * but in LIGHT mode green at 0.18 on white is a pale mint ghost — and at 0%
 * the track is the whole mark, so an empty live project nearly vanished from
 * the tree. Measured by eye in the real app, 2026-09-23. The arc is at full
 * opacity, so 0.3 still leaves a wide gap between done and not done.
 */
export const TRACK_OPACITY = 0.3;
/** The radius left free inside the ring for a mark. */
export const MARK_HOLE = MARK_RADIUS - MARK_STROKE / 2;

/**
 * The arc a picker shows for `active`. A menu has no project in front of it,
 * so its wheel is a glyph, not a measurement — a third of the way round reads
 * as "in progress" without claiming a number.
 */
export const ICON_FRACTION = 0.34;

/** The five shapes, plus the plain ring an unknown state falls back to. */
export type MarkKind = "dashed" | "wheel" | "pause" | "stop" | "check" | "ring";

/**
 * What each run state draws. An exhaustive map, not a switch with a default,
 * so a state added to `PROJECT_STATES` without a mark is caught by a test
 * rather than silently drawing the fallback.
 */
export const STATE_MARK: Readonly<Record<string, MarkKind>> = {
  queued: "dashed",
  active: "wheel",
  on_hold: "pause",
  stopped: "stop",
  done: "check",
};

export function markKind(state: string | null | undefined): MarkKind {
  return (state && STATE_MARK[state.toLowerCase()]) || "ring";
}

/** One drawable piece. `StateMark.tsx` maps each kind to one SVG element. */
export type MarkPart =
  | { kind: "ring"; opacity?: number; dash?: string }
  | { kind: "arc"; dash: string }
  | { kind: "rect"; x: number; y: number; w: number; h: number; rx: number }
  | { kind: "path"; points: ReadonlyArray<readonly [number, number]>; width: number };

const C = MARK_CENTER;
const H = MARK_HOLE;
const round = (n: number) => Math.round(n * 1000) / 1000;

/**
 * The dashes of a queued ring.
 *
 * ⚠️ A WHOLE number of periods around the circle. A dash pattern that does
 * not divide the circumference leaves a seam where the last dash meets the
 * first — a short dash or a double gap at twelve o'clock, which reads as a
 * defect in the icon rather than as "not started".
 *
 * The gap is widened by one stroke because round caps grow each dash by half
 * a stroke at both ends. Without that, the visible gap is almost nothing.
 */
export function queuedDash(segments = 6, visibleGap = 1.7): string {
  const period = (2 * Math.PI * MARK_RADIUS) / segments;
  const gap = visibleGap + MARK_STROKE;
  return `${round(period - gap)} ${round(gap)}`;
}

/** The parts of one state's mark. `progress` only matters for the wheel. */
export function markParts(kind: MarkKind, progress: NodeProgress = {}): MarkPart[] {
  switch (kind) {
    case "dashed":
      return [{ kind: "ring", dash: queuedDash() }];
    case "wheel":
      // ⚠️ NO arc at zero. A zero-length dash with a round cap still paints
      // the cap, so an empty wheel drew a DOT at twelve o'clock — a sliver of
      // progress on a project with none. Seen in the live tree 2026-09-23,
      // and present in every wheel before this family too.
      return completion(progress) > 0
        ? [
            { kind: "ring", opacity: TRACK_OPACITY },
            { kind: "arc", dash: ringDash(progress, MARK_RADIUS) },
          ]
        : [{ kind: "ring", opacity: TRACK_OPACITY }];
    case "pause": {
      const w = H * 0.3;
      const h = H * 1.15;
      const gap = H * 0.26;
      return [
        { kind: "ring" },
        { kind: "rect", x: round(C - gap / 2 - w), y: round(C - h / 2), w: round(w), h: round(h), rx: round(w * 0.42) },
        { kind: "rect", x: round(C + gap / 2), y: round(C - h / 2), w: round(w), h: round(h), rx: round(w * 0.42) },
      ];
    }
    case "stop": {
      // Smaller than the pause pair's footprint on purpose: at 12px the two
      // otherwise both read as "something filled in the middle".
      const s = H * 0.82;
      return [
        { kind: "ring" },
        { kind: "rect", x: round(C - s / 2), y: round(C - s / 2), w: round(s), h: round(s), rx: round(s * 0.18) },
      ];
    }
    case "check":
      return [
        { kind: "ring" },
        {
          kind: "path",
          points: [
            [round(C - H * 0.5), round(C + H * 0.05)],
            [round(C - H * 0.12), round(C + H * 0.42)],
            [round(C + H * 0.52), round(C - H * 0.4)],
          ],
          width: round(MARK_STROKE * 0.67),
        },
      ];
    case "ring":
    default:
      return [{ kind: "ring" }];
  }
}

/**
 * The furthest any inner mark reaches from the centre, stroke included.
 *
 * The fence reads this against `MARK_HOLE`: an inner mark that reaches the
 * ring merges with it, and at 14px that turns a pause or a stop into a disc.
 * That was the failure of the first drawing of this family.
 */
export function innerReach(parts: readonly MarkPart[]): number {
  let reach = 0;
  for (const part of parts) {
    if (part.kind === "rect") {
      // A rounded corner sits inside the square corner, so the square corner
      // is a safe upper bound.
      for (const x of [part.x, part.x + part.w]) {
        for (const y of [part.y, part.y + part.h]) {
          reach = Math.max(reach, Math.hypot(x - C, y - C));
        }
      }
    } else if (part.kind === "path") {
      for (const [x, y] of part.points) {
        reach = Math.max(reach, Math.hypot(x - C, y - C) + part.width / 2);
      }
    }
  }
  return reach;
}
