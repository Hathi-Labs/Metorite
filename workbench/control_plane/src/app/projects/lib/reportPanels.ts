/**
 * WS-27bn R2b — a report section, as the Analytics panel that draws it.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R2b.
 *
 * ⚠️ **One chart seam.** A report section and an Analytics panel draw the
 * same data with the SAME component. These adapters map a section of
 * `GET /projects/reports/{id}/render` to the panel's prop type, and nothing
 * else. A second chart component for the same data is a defect
 * (CLAUDE.md §4).
 *
 * ⚠️ **They copy, and compute nothing.** Every figure is the server's. A
 * field the report does not send stays ABSENT, and the panel draws nothing
 * for it. An adapter that filled a gap with a zero would print a figure the
 * server never said. `reportVisuals.test.ts` scans this file for the
 * arithmetic of `capacity.test.ts`.
 *
 * ⚠️ **Each adapter takes its section as an argument.** `RenderedBody` keeps
 * the literal `sections.<name>` access, which the lockstep test reads.
 */
import type {
  CapacityReport,
  ConflictsReport,
  FinishedReport,
  LoadReport,
  OutlookReport,
  PreviewReportBody,
  RenderedReportBody,
  StuckReport,
  ThroughputReport,
} from "./api";
import { asList } from "./analyticsRead";

type Sections = RenderedReportBody["sections"];

/** What every adapter reads beside its section: the scope and the period. */
export type ReportFrame = Pick<
  RenderedReportBody | PreviewReportBody,
  "report" | "period_start" | "period_end"
>;

function scopeOf(frame: ReportFrame): {
  project_id: string | null;
  scope: "portfolio" | "node";
} {
  return { project_id: frame.report.project_id, scope: frame.report.scope };
}

/** `finished` as `FinishedPanel` takes it. The window is the server's. */
export function finishedPanelData(
  section: NonNullable<Sections["finished"]>,
  frame: ReportFrame
): FinishedReport {
  return {
    ...scopeOf(frame),
    weeks: frame.report.config.weeks,
    skip_current_week: frame.report.config.skip_current_week,
    period_start: frame.period_start,
    period_end: frame.period_end,
    projects: asList<NonNullable<Sections["finished"]>["projects"][number]>(
      section.projects
    ).map((p) => ({ ...p })),
    total_completed: section.total_completed,
    total_cancelled: section.total_cancelled,
  };
}

/**
 * `throughput` as `ThroughputPanel` takes it.
 *
 * The report's summary has no `completed` (its Finished section says it),
 * and its weeks carry only the count. So those stay absent, and the panel
 * hides their cells.
 *
 * `current_week_partial` is the route's own rule (`analytics.py`,
 * `bool(series)`): `weekly_sql` always ends on the running week.
 */
export function throughputPanelData(
  section: NonNullable<Sections["throughput"]>,
  frame: ReportFrame
): ThroughputReport {
  const series = asList<{ week_start: string; completed: number }>(
    section.series
  ).map((w) => ({ week_start: w.week_start, completed: w.completed }));
  return {
    ...scopeOf(frame),
    weeks: frame.report.config.weeks,
    series,
    current_week_partial: series.length > 0,
    summary: {
      median_hours: section.median_hours,
      measured: section.measured,
      p90_hours: section.p90_hours,
      no_start: section.no_start,
      cancelled: section.cancelled,
    },
  };
}

/** `load` as `LoadPanel` takes it. No `people_total` and no `effort`. */
export function loadPanelData(
  section: NonNullable<Sections["load"]>,
  frame: ReportFrame
): LoadReport {
  return {
    ...scopeOf(frame),
    total_tasks: section.total_tasks,
    people: asList<NonNullable<Sections["load"]>["people"][number]>(
      section.people
    ).map((p) => ({ ...p })),
  };
}

/** `capacity` as `CapacityPanel` takes it: the report's `people` are `rows`. */
export function capacityPanelData(
  section: NonNullable<Sections["capacity"]>,
  frame: ReportFrame
): CapacityReport {
  return {
    ...scopeOf(frame),
    horizon_days: section.horizon_days,
    hr_visible: section.hr_visible,
    windows: section.windows,
    total_tasks: section.total_tasks,
    people_total: section.people_total,
    rows: asList<CapacityReport["rows"][number]>(section.people),
  };
}

/**
 * `stuck` as `StuckPanel` takes it: the overdue half, and the ageing bands.
 *
 * WS-27bn R3a. The report carries the route's `stale` bands, so the panel
 * draws its band chart. A body from a server before R3a has no `stale`, and
 * the key stays ABSENT, so the panel draws no band chart and never says
 * "No open work in this scope" about work it was not sent.
 */
export function stuckPanelData(
  section: NonNullable<Sections["stuck"]>,
  frame: ReportFrame
): StuckReport {
  return {
    ...scopeOf(frame),
    overdue: asList<StuckReport["overdue"][number]>(section.overdue),
    overdue_total: section.overdue_total,
    ...(section.stale === undefined || section.stale === null
      ? {}
      : { stale: asList<{ band: string; n: number }>(section.stale) }),
  };
}

/**
 * `outlook` as `OutlookPanel` takes it (WS-27bn R3a).
 *
 * The section IS the outlook route's body, so this copies it. It takes no
 * frame: the scope is the section's own, because the server resolved it.
 */
export function outlookPanelData(
  section: NonNullable<Sections["outlook"]>
): OutlookReport {
  return { ...section };
}

/** `conflicts` as `ConflictsPanel` takes it. The rows are the server's. */
export function conflictsPanelData(
  section: NonNullable<Sections["conflicts"]>,
  frame: ReportFrame
): ConflictsReport {
  return {
    ...scopeOf(frame),
    include_subtree: frame.report.config.include_subtree,
    horizon_days: section.horizon_days,
    hr_visible: section.hr_visible,
    window: section.window,
    total: section.total,
    by_kind: section.by_kind ?? {},
    rows: asList<ConflictsReport["rows"][number]>(section.rows),
  };
}

/** One summary tile: one figure from one section. */
export interface ReportTile {
  key: "finished" | "open" | "overdue" | "median";
  label: string;
  /** A count. Absent on the median tile. */
  value?: number;
  /** The median tile only: hours, or `null` for "not measured". */
  hours?: number | null;
  /** The state the figure means, for `statusAccent`. */
  tone?: "done" | "late";
  title: string;
}

/**
 * The four tiles above the sections, in order. A tile whose section is not
 * in the report is left out, so a tile never shows a figure nobody asked for.
 *
 * ⚠️ **Overdue is `stuck.overdue_total`, never a sum of the rows per
 * person.** A task with two assignees sits in two rows, so a sum counts it
 * twice.
 */
export function reportTiles(sections: Sections): ReportTile[] {
  const tiles: ReportTile[] = [];
  if (sections.finished) {
    tiles.push({
      key: "finished",
      label: "Finished",
      value: sections.finished.total_completed,
      tone: "done",
      title: "Tasks that reached a done status in this period.",
    });
  }
  const open = sections.load ?? sections.capacity;
  if (open) {
    tiles.push({
      key: "open",
      label: "Open",
      value: open.total_tasks,
      title:
        "Open tasks in this scope, counted over tasks. A task with two assignees counts once.",
    });
  }
  if (sections.stuck) {
    tiles.push({
      key: "overdue",
      label: "Overdue",
      value: sections.stuck.overdue_total,
      tone: "late",
      title: "Open tasks past their due date, counted over tasks.",
    });
  }
  if (sections.throughput) {
    tiles.push({
      key: "median",
      label: "Median cycle time",
      hours: sections.throughput.median_hours,
      title:
        sections.throughput.median_hours === null
          ? "No task in this period recorded both a start and a finish."
          : `Half of the ${sections.throughput.measured} measured tasks took less than this, from first In progress until done.`,
    });
  }
  return tiles;
}
