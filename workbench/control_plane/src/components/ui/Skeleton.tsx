"use client";

/**
 * Skeleton — the shape of content that has not arrived.
 *
 * ⚠️ THIS IS A PERFORMANCE FEATURE, not decoration. A skeleton in the shape of
 * the real layout reads as roughly twice as fast as a line of text at the SAME
 * latency, because the eye is given structure to settle on and the page does
 * not jump when the rows land. "Loading projects…" on an empty panel is the
 * slowest-feeling thing a surface can do — it says *wait* and shows nothing to
 * wait at.
 *
 * WHY IT LIVES HERE. `animate-pulse` was hand-rolled in twenty files with
 * twenty different bar heights and radii, which is rule 3 of AGENTS.md — never
 * hand-roll a control — one level below where anyone was looking. One
 * primitive, house tokens, so a density or radius change moves all of them.
 *
 *     <Skeleton className="h-4 w-32" />
 *     <SkeletonRows count={6} />
 *     <SkeletonBoard columns={4} />
 *
 * The caller sets the SIZE (`h-*`/`w-*`); the primitive owns the fill, the
 * radius and the animation. `bg-muted` is the token for quiet — never a colour.
 */

/** One bar. Give it a height and a width. */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`animate-pulse rounded-md bg-muted ${className}`}
    />
  );
}

/**
 * A stack of list rows.
 *
 * The widths STEP rather than repeat. A column of identical bars reads as a
 * loading graphic; uneven ones read as text, which is what is actually coming.
 */
export function SkeletonRows({
  count = 6,
  className = "",
}: {
  count?: number;
  className?: string;
}) {
  const widths = ["w-full", "w-11/12", "w-4/5", "w-full", "w-3/4", "w-10/12"];
  return (
    <div
      className={`space-y-2 ${className}`}
      role="status"
      aria-busy="true"
      aria-label="Loading"
    >
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} className={`h-9 ${widths[i % widths.length]}`} />
      ))}
    </div>
  );
}

/**
 * The board's shape: columns of cards.
 *
 * Deliberately mirrors the real board's spacing, because the point is that
 * nothing MOVES when the data lands. A skeleton at the wrong geometry is worse
 * than none — it promises a layout and then replaces it with another.
 */
export function SkeletonBoard({
  columns = 4,
  className = "",
}: {
  columns?: number;
  className?: string;
}) {
  const cards = [3, 2, 4, 2, 3, 2];
  return (
    <div
      className={`flex gap-3 overflow-hidden p-3 ${className}`}
      role="status"
      aria-busy="true"
      aria-label="Loading"
    >
      {Array.from({ length: columns }, (_, col) => (
        <div key={col} className="w-64 shrink-0 space-y-2">
          <Skeleton className="h-6 w-28" />
          {Array.from({ length: cards[col % cards.length] }, (_, card) => (
            <Skeleton key={card} className="h-16 w-full" />
          ))}
        </div>
      ))}
    </div>
  );
}

/** The project rail: a nested tree of short labels. */
export function SkeletonTree({ count = 7 }: { count?: number }) {
  const indent = ["", "ml-3", "ml-3", "", "ml-3", "ml-6", ""];
  const widths = ["w-32", "w-24", "w-28", "w-36", "w-20", "w-24", "w-28"];
  return (
    <div className="space-y-2 p-3" role="status" aria-busy="true" aria-label="Loading">
      {Array.from({ length: count }, (_, i) => (
        <Skeleton
          key={i}
          className={`h-5 ${widths[i % widths.length]} ${indent[i % indent.length]}`}
        />
      ))}
    </div>
  );
}

export default Skeleton;
