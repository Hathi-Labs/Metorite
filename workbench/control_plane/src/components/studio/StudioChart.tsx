"use client";

/**
 * The Chart block (`ai_studio.md` §5.1, slice STU-D4).
 *
 * It draws the result of a C2b aggregate in any of the 13 kinds of `lib/charts/kinds.mjs`.
 * It adds no chart code: `aggregateModel.ts` builds the short spec, and `LiveChart`, the one
 * live chart of the product, draws it with the member's mode, density and number system.
 * An aggregate with no group says "No data". A result that the kind cannot draw says why.
 * More groups than the cap show the largest, and a line says how many are left out.
 */
import { useMemo } from "react";

import LiveChart from "@/components/LiveChart";
import { chartFromAggregate, parseAggregate } from "@/lib/studio/aggregateModel";
import type { Row } from "@/lib/studio/dataDoor";

export function ChartEmpty({ message = "No data" }: { message?: string }) {
  return (
    <div role="status" data-chart-empty="" className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
      {message}
    </div>
  );
}

export function StudioChart({ data, rows }: { data: unknown; rows: Row[] }) {
  const d = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const kind = typeof d.type === "string" ? d.type.trim().toLowerCase() : "";
  const title = typeof d.title === "string" ? d.title : undefined;
  const subtitle = typeof d.subtitle === "string" ? d.subtitle : undefined;
  const built = useMemo(
    () => chartFromAggregate(kind, parseAggregate(rows), { title, subtitle }),
    [kind, rows, title, subtitle],
  );
  if (!built.ok) return <ChartEmpty message={built.reason} />;
  return (
    <div className="min-w-0 space-y-1" data-studio-chart={kind}>
      <LiveChart spec={built.spec} label={title ?? "Chart"} />
      {built.note && <p data-chart-note="" className="text-xs text-muted-foreground">{built.note}</p>}
    </div>
  );
}

export default StudioChart;
