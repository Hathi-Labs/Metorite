"use client";

/**
 * The Stat block (`ai_studio.md` §5.1, slice STU-D4).
 *
 * One tile: the count, the sum or the average of a C2b aggregate over every record, with its
 * trend against the period before. The trend needs an aggregate grouped by day or by month.
 * With no such group the tile shows the value alone and says so. The colour of the trend is
 * a status hue from `statusAccent.ts`, and `good: "down"` makes a fall the good change.
 */
import { useMemo } from "react";

import Icon from "@/components/Icon";
import { TYPE } from "@/lib/typeScale";
import { accentForHue } from "@/lib/statusAccent";
import { parseAggregate, statFromAggregate } from "@/lib/studio/aggregateModel";
import type { Row } from "@/lib/studio/dataDoor";

const W = 120;
const H = 32;

/** The points of the small line, from the period values. */
export function sparkPoints(values: readonly number[]): string {
  if (values.length < 2) return "";
  const lo = Math.min(...values);
  const range = Math.max(...values) - lo || 1;
  return values.map((v, i) => `${((i / (values.length - 1)) * W).toFixed(1)},${(H - ((v - lo) / range) * H).toFixed(1)}`).join(" ");
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
  const goodDown = d.good === "down";
  const t = tile.trend;
  const fine = t ? (t.delta > 0) !== goodDown : true;
  const tone = t && t.delta !== 0 ? accentForHue(fine ? "green" : "red").text : "text-muted-foreground";
  return (
    <div data-studio-stat="" className="min-w-0 rounded-xl border border-border bg-card p-3 sm:p-4">
      <div className="truncate text-xs text-muted-foreground" title={heading || tile.label}>{heading || tile.label}</div>
      <div className="mt-1 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div data-stat-value="" className="truncate font-semibold tabular-nums text-foreground" style={{ fontSize: TYPE["2xl"] }}>
            {tile.value}
          </div>
          {t ? (
            <div data-stat-trend="" className={`mt-0.5 text-xs tabular-nums ${tone}`}>
              {t.delta !== 0 && <Icon name={t.delta > 0 ? "ArrowUp" : "ArrowDown"} size={11} className="mr-0.5 inline align-[-1px]" />}
              {t.delta === 0 ? "No change" : t.text}
              <span className="text-muted-foreground"> vs {t.against}</span>
            </div>
          ) : (
            <div data-stat-trend="none" className="mt-0.5 text-xs text-muted-foreground">No trend. Group by a date for one.</div>
          )}
        </div>
        {tile.spark.length > 1 && (
          <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden className="shrink-0 text-primary">
            <polyline points={sparkPoints(tile.spark)} fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </div>
    </div>
  );
}

export default StudioStat;
