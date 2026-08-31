"use client";

/**
 * Analytics — the portfolio, in Plane's shape (owner ask 2026-08-31).
 *
 * Patterned on Plane @ effd0c5: the KPI strip is
 * `analytics/total-insights.tsx` + `insight-card.tsx` (a label over a
 * number), and the table is `analytics/insight-table/` +
 * `work-items/workitems-insight-table.tsx` — one row per project, one
 * right-aligned column per state group, with the entity's own icon in the
 * first cell. Ours rolls up SPACES instead of projects, adds a totals
 * footer so the table reconciles with the strip above it, and swaps
 * Plane's TanStack table for plain markup — sorting one screen of spaces
 * is not worth a dependency.
 *
 * What is deliberately NOT here: Plane's created-vs-resolved area chart
 * and its custom X/Y insight builder need a by-month time series, and
 * `GET /summary` does not carry one. A chart drawn from data we do not
 * have would be an invented trend line. When a time-series endpoint
 * exists, that section slots in below the table.
 *
 * ⚠️ Every count is the CALLER'S — the same visibility clause as the
 * dashboard (`NodeDashboard` header says why). Every hue resolves through
 * `statusAccent`; the table paints no colour of its own.
 */
import Icon from "@/components/Icon";
import { statusAccent } from "@/lib/statusAccent";
import { accentForSlot } from "@/lib/categorical";

import type { NodeSummary, SummaryChild } from "../lib/api";
import { spaceMarker } from "../lib/tree";

/** The lanes as table columns, board order. Cancelled earns no column of
 *  its own until somebody cancels something — see `columns()` below. */
const COLUMN_ORDER = [
  "triage",
  "backlog",
  "todo",
  "in_progress",
  "done",
  "cancelled",
] as const;

const COLUMN_LABELS: Record<string, string> = {
  triage: "Triage",
  backlog: "Backlog",
  todo: "To do",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};

/**
 * Which category columns the table draws: every ordered lane with at least
 * one task anywhere, plus any category the client has not learned. An
 * all-zero column is noise; a dropped non-zero one would make the row sums
 * disagree with the Total column.
 */
function columns(summary: NodeSummary): string[] {
  const seen = new Set<string>();
  for (const child of summary.children) {
    for (const [key, count] of Object.entries(child.by_category)) {
      if (count > 0) seen.add(key);
    }
  }
  for (const [key, count] of Object.entries(summary.by_category)) {
    if (count > 0) seen.add(key);
  }
  const known = COLUMN_ORDER.filter((c) => seen.has(c));
  const extra = [...seen].filter((c) => !COLUMN_ORDER.includes(c as never)).sort();
  return [...known, ...extra];
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className={`text-lg font-semibold ${tone ?? "text-foreground"}`}>{value}</p>
    </div>
  );
}

export default function AnalyticsView({
  summary,
  onOpen,
}: {
  /** The PORTFOLIO roll-up — every space the caller can see. */
  summary: NodeSummary;
  onOpen: (id: string) => void;
}) {
  const by = summary.by_category;
  const cats = columns(summary);
  const done = by.done ?? 0;
  const inProgress = by.in_progress ?? 0;

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <p className="mb-3 text-[11px] uppercase tracking-wider text-muted-foreground">
        Analytics
      </p>

      {/* The KPI strip — Plane's total-insights row, our Stat idiom. */}
      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Spaces" value={summary.children.length} />
        <Stat label="Projects" value={summary.projects} />
        <Stat label="Tasks" value={summary.tasks} />
        <Stat label="In progress" value={inProgress} />
        <Stat
          label="Overdue"
          value={summary.overdue}
          tone={
            summary.overdue > 0
              ? statusAccent({ category: "cancelled" }).text
              : undefined
          }
        />
      </div>

      {/* The insight table — Plane's per-project state matrix, over spaces. */}
      {summary.children.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border bg-card text-left">
                <th className="px-3 py-2 font-medium text-muted-foreground">
                  Space
                </th>
                {cats.map((cat) => (
                  <th
                    key={cat}
                    className="px-3 py-2 text-right font-medium text-muted-foreground"
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className={`h-2 w-2 rounded-full ${statusAccent({ category: cat }).dot}`}
                      />
                      {COLUMN_LABELS[cat] ?? cat}
                    </span>
                  </th>
                ))}
                <th className="px-3 py-2 text-right font-medium text-muted-foreground">
                  Overdue
                </th>
                <th className="px-3 py-2 text-right font-medium text-muted-foreground">
                  Total
                </th>
              </tr>
            </thead>
            <tbody>
              {summary.children.map((child) => (
                <SpaceRow key={child.id} child={child} cats={cats} onOpen={onOpen} />
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border bg-card font-medium">
                <td className="px-3 py-2">All spaces</td>
                {cats.map((cat) => (
                  <td key={cat} className="px-3 py-2 text-right">
                    {by[cat] ?? 0}
                  </td>
                ))}
                <td className="px-3 py-2 text-right">{summary.overdue}</td>
                <td className="px-3 py-2 text-right">{summary.tasks}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      ) : (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
          No spaces yet. Create one with the + beside Spaces.
        </p>
      )}
    </div>
  );
}

function SpaceRow({
  child,
  cats,
  onOpen,
}: {
  child: SummaryChild;
  cats: string[];
  onOpen: (id: string) => void;
}) {
  // The space's own marker, exactly as the sidebar draws it — Plane's
  // insight table leads with the project logo for the same reason: a row
  // you can recognise without reading.
  const marker = spaceMarker(child);
  return (
    <tr
      className="cursor-pointer border-b border-border last:border-b-0 hover:bg-muted"
      onClick={() => onOpen(child.id)}
    >
      <td className="px-3 py-2">
        <span className="flex min-w-0 items-center gap-2">
          <Icon
            name={marker.icon}
            className={`h-3.5 w-3.5 shrink-0 ${accentForSlot(marker.slot).text}`}
          />
          <span className="truncate font-medium text-foreground">{child.name}</span>
        </span>
      </td>
      {cats.map((cat) => {
        const count = child.by_category[cat] ?? 0;
        return (
          <td
            key={cat}
            className={`px-3 py-2 text-right ${count === 0 ? "text-muted-foreground/50" : "text-foreground"}`}
          >
            {count}
          </td>
        );
      })}
      <td
        className={`px-3 py-2 text-right ${
          child.overdue > 0
            ? statusAccent({ category: "cancelled" }).text
            : "text-muted-foreground/50"
        }`}
      >
        {child.overdue}
      </td>
      <td className="px-3 py-2 text-right text-foreground">{child.tasks}</td>
    </tr>
  );
}
