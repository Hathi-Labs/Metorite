/**
 * Timeline block · the grid chrome.
 *
 * The two header tiers, the column shading and the today line. Moved from
 * `app/projects/components/TimelineView.tsx` (STU-D6) with the same markup and
 * the same classes. It reads cells, which `geometry.ts` builds. It knows
 * nothing about a row.
 */

import { ZOOMS, type TimelineRange, type TimelineZoom } from "./scale";
import type { DayCell, MonthCell, WeekCell } from "./geometry";

/**
 * Two tiers: months, then days or weeks depending on the zoom. One tier could
 * not say both "which month" and "which day".
 *
 * `labelInsetPx` is how far a sticky month label stays from the left. A caller
 * with a sticky column of its own passes that column's width plus a gap.
 */
export function TimelineHeader({
  zoom,
  months,
  days,
  weeks,
  heightPx,
  labelInsetPx,
}: {
  zoom: TimelineZoom;
  months: readonly MonthCell[];
  days: readonly DayCell[];
  weeks: readonly WeekCell[];
  heightPx: number;
  labelInsetPx: number;
}) {
  return (
    <div
      className="sticky top-0 z-20 border-b border-border bg-muted"
      style={{ height: heightPx }}
    >
      <div className="relative" style={{ height: heightPx / 2 }}>
        {months.map((cell) => (
          <div
            key={cell.key}
            className="absolute top-0 flex h-full items-center border-r border-border/60 px-2 text-xs font-medium text-foreground"
            style={{ left: cell.px, width: cell.widthPx }}
          >
            {/* The label is STICKY, or it leaves with its own month. Scrolling
                a few days into September left "026" on screen. It sticks past
                the caller's own sticky column, which would cover it. */}
            <span className="sticky whitespace-nowrap" style={{ left: labelInsetPx }}>
              {cell.widthPx > 56 ? cell.label : ""}
            </span>
          </div>
        ))}
      </div>
      <div
        className="relative border-t border-border/40"
        style={{ height: heightPx / 2 }}
      >
        {ZOOMS[zoom].tier === "day"
          ? days.map((cell) => (
              <div
                key={cell.day}
                className={`absolute top-0 flex h-full flex-col items-center justify-center border-r border-border/40 text-[10px] leading-none ${
                  cell.today
                    ? "font-semibold text-primary"
                    : cell.weekend
                      ? "text-muted-foreground/60"
                      : "text-muted-foreground"
                }`}
                style={{ left: cell.px, width: cell.widthPx }}
              >
                <span>{cell.label}</span>
                <span className="mt-0.5">{cell.date}</span>
              </div>
            ))
          : weeks.map((cell) => (
              <div
                key={cell.key}
                className={`absolute top-0 flex h-full items-center justify-center border-r border-border/40 text-[10px] ${
                  cell.today ? "font-semibold text-primary" : "text-muted-foreground"
                }`}
                style={{ left: cell.px, width: cell.widthPx }}
              >
                {cell.widthPx > 18 ? cell.label : ""}
              </div>
            ))}
      </div>
    </div>
  );
}

/**
 * The columns under the bars: weekend shading, week rules and the today line.
 *
 * Weekends are shaded only where a column is wide enough to read as a column.
 * At quarter zoom a 7px band is visual noise, so week rules draw instead.
 */
export function TimelineColumns({
  range,
  days,
  todayPx,
  heightPx,
}: {
  range: TimelineRange;
  days: readonly DayCell[];
  todayPx: number | null;
  heightPx: number;
}) {
  return (
    <>
      {range.pxPerDay >= 12
        ? days
            .filter((cell) => cell.weekend)
            .map((cell) => (
              <div
                key={cell.day}
                className="absolute top-0 bg-muted/40"
                style={{ left: cell.px, width: cell.widthPx, height: heightPx }}
              />
            ))
        : null}
      {range.pxPerDay < 12
        ? days
            .filter((cell) => cell.weekStart)
            .map((cell) => (
              <div
                key={cell.day}
                className="absolute top-0 w-px bg-border/40"
                style={{ left: cell.px, height: heightPx }}
              />
            ))
        : null}

      {todayPx !== null ? (
        <div
          className="absolute top-0 z-0 bg-primary/50"
          style={{ left: todayPx, width: Math.max(2, range.pxPerDay), height: heightPx }}
          title="Today"
        />
      ) : null}
    </>
  );
}
