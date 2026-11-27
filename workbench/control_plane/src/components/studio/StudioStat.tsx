"use client";

/**
 * The Stat block (`ai_studio.md` §5.1, slice STU-D4).
 *
 * One tile: the count, the sum or the average of a C2b aggregate over every record, with its
 * trend against the period before. The trend needs an aggregate grouped by day or by month.
 * With no such group the tile shows the value alone and says so. The colour of the trend is
 * a status hue from `statusAccent.ts`, and `good: "down"` makes a fall the good change.
 *
 * The value is one line, and it is never cut. A number that breaks between two digits reads as
 * another number (STU-LY-7). So the type size follows the width of the tile (`valueEms`), and a
 * dashboard gives a long value the full row (`data-stat-wide`).
 */
import { useMemo } from "react";

import Icon from "@/components/Icon";
import { TYPE } from "@/lib/typeScale";
import { accentForHue } from "@/lib/statusAccent";
import { parseAggregate, statFromAggregate } from "@/lib/studio/aggregateModel";
import type { Row } from "@/lib/studio/dataDoor";
import { NAV_ICONS } from "@/lib/studio/manifest";

const W = 120;
const H = 32;

/** The points of the small line, from the period values. */
export function sparkPoints(values: readonly number[]): string {
  if (values.length < 2) return "";
  const lo = Math.min(...values);
  const range = Math.max(...values) - lo || 1;
  return values.map((v, i) => `${((i / (values.length - 1)) * W).toFixed(1)},${(H - ((v - lo) / range) * H).toFixed(1)}`).join(" ");
}

/** A value wider than this, in em, takes the full row of a dashboard. */
export const WIDE_VALUE_EMS = 9;

/**
 * The width of a value in em, for one line of semibold tabular figures. It is a little over the
 * true width on purpose, so the value never passes the edge of its tile.
 */
export function valueEms(value: string): number {
  let w = 0;
  for (const ch of value) w += /[0-9]/.test(ch) ? 0.7 : /[.,:' \u00a0\u202f]/.test(ch) ? 0.35 : 0.95;
  return Math.max(1, Math.round(w * 100) / 100);
}

export function StudioStat({ data, rows }: { data: unknown; rows: Row[] }) {
  const d = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const j = typeof d.measure === "number" ? d.measure : 0;
  const tile = useMemo(() => statFromAggregate(parseAggregate(rows), j), [rows, j]);
  const heading = typeof d.label === "string" && d.label.trim() ? d.label : typeof d.title === "string" ? d.title : "";
  if (!tile) {
    return (
      <div role="status" data-stat-empty="" className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
        No data
      </div>
    );
  }
  // An icon of the icon list before the heading (STU-LOOK-2). A name outside the list draws nothing.
  const iconName = typeof d.icon === "string" && (NAV_ICONS as readonly string[]).includes(d.icon) ? d.icon : null;
  const goodDown = d.good === "down";
  const t = tile.trend;
  const fine = t ? (t.delta > 0) !== goodDown : true;
  const tone = t && t.delta !== 0 ? accentForHue(fine ? "green" : "red").text : "text-muted-foreground";
  const ems = valueEms(tile.value);
  return (
    <div data-studio-stat="" data-stat-wide={ems > WIDE_VALUE_EMS ? "" : undefined} className="min-w-0 rounded-xl border border-border bg-card p-3 sm:p-4">
      <div className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground" title={heading || tile.label}>
        {iconName ? <Icon name={iconName} size={14} className="shrink-0" aria-hidden /> : null}
        <span className="truncate" data-stat-heading="">{heading || tile.label}</span>
      </div>
      {/* The value has a row of its own. `100cqi` is the width of that row. */}
      <div className="@container mt-1">
        <div data-stat-value="" className="whitespace-nowrap font-semibold tabular-nums text-foreground"
          style={{ fontSize: `min(${TYPE["2xl"]}, calc(100cqi / ${ems}))` }}>
          {tile.value}
        </div>
      </div>
      {/* In a narrow tile the small line goes under the trend. */}
      <div className="mt-0.5 flex flex-wrap items-end justify-between gap-x-3 gap-y-2">
        <div className="min-w-0 max-w-full">
          {t ? (
            <div data-stat-trend="" className={`text-xs tabular-nums ${tone}`}>
              {t.delta !== 0 && <Icon name={t.delta > 0 ? "ArrowUp" : "ArrowDown"} size={11} className="mr-0.5 inline align-[-1px]" />}
              <span className="whitespace-nowrap">{t.delta === 0 ? "No change" : t.text}</span>
              {/* A date such as 2026-11-03 stays on one line. */}
              <span className="text-muted-foreground"> vs <span data-stat-against="" className="whitespace-nowrap">{t.against}</span></span>
            </div>
          ) : (
            // A member needs no builder hint here. The line keeps its height, so the tiles of a row stay level.
            <div data-stat-trend="none" aria-hidden className="text-xs text-muted-foreground">{"\u00a0"}</div>
          )}
        </div>
        {tile.spark.length > 1 && (
          <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden className="max-w-full shrink-0 text-primary">
            <polyline points={sparkPoints(tile.spark)} fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </div>
    </div>
  );
}

export default StudioStat;
