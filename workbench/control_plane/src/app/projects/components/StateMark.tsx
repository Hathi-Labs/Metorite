"use client";

/**
 * Projects · the run-state mark, drawn.
 *
 * Draws exactly what `lib/stateMark.ts` returns and decides nothing. The
 * rules — which state gets which shape, how thick, how far a mark may reach —
 * live in that module, where a test can see them (D-PM-21).
 *
 * ⚠️ `currentColor` everywhere. The hue comes from the caller's `className`
 * (`projectStateAccent(state).text`), so this adds no colour vocabulary of its
 * own (CLAUDE.md §4) and follows light mode and the accent like any glyph.
 *
 * 🔴 **NO `<title>` CHILD. The label is an attribute, never text.** An SVG
 * `<title>` is TEXT CONTENT. The first wheel put the project name there, so
 * every row held an invisible second copy of its own name, BEFORE the visible
 * one in DOM order. `getByText(name).first()` resolved to a node nobody can
 * click, four browser tests hung for two minutes each, and the CI job ran out
 * of time and reported "cancelled". `aria-label` on a `role="img"` carries the
 * accessible name, and the wrapper's `title` attribute carries the tooltip.
 */
import type { ThemedIcon } from "@/components/Icon";
import { projectStateAccent } from "@/lib/statusAccent";

import type { NodeProgress } from "../lib/progressWheel";
import {
  ICON_FRACTION,
  MARK_BOX,
  MARK_CENTER,
  MARK_RADIUS,
  MARK_STROKE,
  type MarkPart,
  markKind,
  markParts,
} from "../lib/stateMark";

function Part({ part }: { part: MarkPart }) {
  switch (part.kind) {
    case "ring":
      return (
        <circle
          cx={MARK_CENTER}
          cy={MARK_CENTER}
          r={MARK_RADIUS}
          fill="none"
          stroke="currentColor"
          strokeWidth={MARK_STROKE}
          strokeOpacity={part.opacity}
          strokeDasharray={part.dash}
          strokeLinecap={part.dash ? "round" : undefined}
        />
      );
    case "arc":
      return (
        <circle
          cx={MARK_CENTER}
          cy={MARK_CENTER}
          r={MARK_RADIUS}
          fill="none"
          stroke="currentColor"
          strokeWidth={MARK_STROKE}
          strokeLinecap="round"
          strokeDasharray={part.dash}
        />
      );
    case "rect":
      return (
        <rect
          x={part.x}
          y={part.y}
          width={part.w}
          height={part.h}
          rx={part.rx}
          fill="currentColor"
        />
      );
    case "path":
      return (
        <path
          d={`M ${part.points.map(([x, y]) => `${x} ${y}`).join(" L ")}`}
          fill="none"
          stroke="currentColor"
          strokeWidth={part.width}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      );
  }
}

export function StateMark({
  state,
  progress,
  className,
  label,
}: {
  state: string;
  progress?: NodeProgress;
  className?: string;
  /** The accessible name. Omit it where a text label already sits beside. */
  label?: string;
}) {
  const parts = markParts(markKind(state), progress);
  return (
    // The tooltip rides on the WRAPPER as an attribute. `title` on a span is
    // not text content, so it adds no second match for the project name.
    <span className={`inline-flex ${className ?? ""}`} title={label}>
      <svg
        viewBox={`0 0 ${MARK_BOX} ${MARK_BOX}`}
        className="h-full w-full"
        role={label ? "img" : undefined}
        aria-label={label}
        aria-hidden={label ? undefined : true}
      >
        {/* Rotated so the dashes and the arc both start at twelve o'clock.
            SVG strokes a circle from three o'clock, and a wheel that fills
            from the right reads as a different quantity than its number. */}
        <g transform={`rotate(-90 ${MARK_CENTER} ${MARK_CENTER})`}>
          {parts
            .filter((p) => p.kind === "ring" || p.kind === "arc")
            .map((p, i) => (
              <Part key={i} part={p} />
            ))}
        </g>
        {parts
          .filter((p) => p.kind === "rect" || p.kind === "path")
          .map((p, i) => (
            <Part key={i} part={p} />
          ))}
      </svg>
    </span>
  );
}

const boundMarks = new Map<string, ThemedIcon>();

/**
 * A run-state mark in the shape `ContextMenu` takes for an icon.
 *
 * The run-state picker is where a member CHANGES the state, so it draws the
 * same marks the tree does — a picker in one icon set beside a tree in another
 * is the discontinuity this family exists to remove. The hue is bound in,
 * because `ContextMenu` passes only a size class. Decorative: the menu row
 * already names the state in text.
 */
export function stateMarkIcon(state: string): ThemedIcon {
  const cached = boundMarks.get(state);
  if (cached) return cached;
  const hue = projectStateAccent(state).text;
  const Bound: ThemedIcon = ({ className }) => (
    <StateMark
      state={state}
      progress={{ tasks: 100, done: Math.round(ICON_FRACTION * 100) }}
      className={`${className ?? ""} ${hue}`}
    />
  );
  Bound.displayName = `StateMark(${state})`;
  boundMarks.set(state, Bound);
  return Bound;
}
