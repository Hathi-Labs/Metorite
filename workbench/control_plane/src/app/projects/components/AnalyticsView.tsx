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
  for (const child of summary.children ?? []) {
    for (const [key, count] of Object.entries(child.by_category ?? {})) {
      if (count > 0) seen.add(key);
    }
  }
  for (const [key, count] of Object.entries(summary.by_category ?? {})) {
    if (count > 0) seen.add(key);
  }
  const known = COLUMN_ORDER.filter((c) => seen.has(c));
  const extra = [...seen].filter((c) => !COLUMN_ORDER.includes(c as never)).sort();
  return [...known, ...extra];
}

/**
 * One figure, and never a blank.
 *
 * ⚠️ `value` is TYPED as a number and is not guaranteed to be one. `api.call`
 * parses JSON and casts it, so a roll-up that omits `projects` or `tasks`
 * arrives as `undefined` — and `{undefined}` renders NOTHING. Measured
 * 2026-09-03: two of the five tiles were a heading over empty space.
 *
 * A tile whose whole purpose is one figure, showing no figure, is worse than a
 * tile that is absent. It reads as a number that FAILED, not as a number that
 * is zero, and the reader cannot tell which. Zero is "0". An unavailable
 * figure is an explicit dash that says so.
 */
function Stat({
  label,
  value,
  tone,
  className = "",
}: {
  label: string;
  value?: number;
  tone?: string;
  className?: string;
}) {
  const known = typeof value === "number" && Number.isFinite(value);
  return (
    <div className={`rounded-lg border border-border bg-card px-3 py-2 ${className}`}>
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p
        className={`text-lg font-semibold ${known ? (tone ?? "text-foreground") : "text-muted-foreground"}`}
        title={known ? undefined : `${label} did not come back from the server`}
      >
        {known ? value : "—"}
      </p>
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
  /**
   * ⚠️ Read the roll-up defensively, for the same reason `Stat` does.
   *
   * `children` and `by_category` are typed as present and are not guaranteed
   * to be. A response without `children` threw on `.length` before a single
   * tile rendered — the whole pane, gone, for one absent key. `hasChildren`
   * keeps the DIFFERENCE that the empty state depends on: absent means the
   * server did not say, and `[]` means it said none.
   */
  const hasChildren = Array.isArray(summary.children);
  const children = hasChildren ? summary.children : [];
  const by = summary.by_category ?? {};
  const cats = columns({ ...summary, children, by_category: by });
  const done = by.done ?? 0;
  const inProgress = by.in_progress ?? 0;

  return (
    <div className="flex-1 overflow-y-auto p-4">
      {/* The KPI strip — Plane's total-insights row, our Stat idiom. */}
      {/* ⚠️ FIVE tiles, and 5 divides by neither 2 nor 3. So the last one sat
          alone on its own row at phone and tablet width — an orphan, which
          reads as a tile that failed to load rather than as the fifth of five.
          It spans the leftover columns instead, until all five fit in one row. */}
      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Spaces" value={hasChildren ? children.length : undefined} />
        <Stat label="Projects" value={summary.projects} />
        <Stat label="Tasks" value={summary.tasks} />
        <Stat label="In progress" value={inProgress} />
        <Stat
          label="Overdue"
          value={summary.overdue}
          // The fifth of five. See the grid note above.
          className="col-span-2 sm:col-span-1"
          tone={
            summary.overdue > 0
              ? statusAccent({ category: "cancelled" }).text
              : undefined
          }
        />
      </div>

      {/* The insight table — Plane's per-project state matrix, over spaces. */}
      {children.length > 0 ? (
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
              {children.map((child) => (
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
        /* ⚠️ Two different facts, two different sentences.
           This read "No spaces yet" whatever the reason, so a roll-up that
           came back without its children said "nothing here" beside a rail
           that listed six spaces. An empty state is a claim about the world,
           and a false one sends the reader to create what they already have.
           `children` absent means the server did not tell us. An empty array
           means it did, and the answer was none. */
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
          {hasChildren
            ? "No spaces yet. Create one with the + beside Spaces."
            : "Could not load the roll-up. The spaces in the rail are unaffected."}
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
        const count = (child.by_category ?? {})[cat] ?? 0;
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
