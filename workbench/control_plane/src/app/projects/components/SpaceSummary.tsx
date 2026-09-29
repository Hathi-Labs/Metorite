"use client";

/**
 * The KPI strip and the space table, above the sections of Overview.
 *
 * WS-27bn R5f (`projects_reports.md` §8 R5f rules 6 to 9). This markup was
 * the top and the bottom of `AnalyticsView.tsx`, and it moved here when the
 * Analytics app joined Reports. It has one home. Do not copy it.
 *
 * Patterned on Plane @ effd0c5: the strip is `analytics/total-insights.tsx`
 * + `insight-card.tsx` (a label over a number), and the table is
 * `analytics/insight-table/` + `work-items/workitems-insight-table.tsx`. One
 * row for each child, one right-aligned column for each state group, with the
 * child's own icon in the first cell. Ours rolls up SPACES for the whole
 * organization, and the children of a node for a node. A totals footer makes
 * the table agree with the strip. Plain markup, and no table library.
 *
 * ⚠️ **The strip has no Overdue tile.** The report tiles under the header
 * show Overdue from the `stuck` section, and one screen shows one Overdue
 * tile (rule 9). The table keeps its Overdue column, which `/summary`
 * counts.
 *
 * ⚠️ Every count is the CALLER'S: the same visibility clause as the
 * dashboard (`NodeDashboard` header says why). Every hue resolves through
 * `statusAccent`, and the table paints no colour of its own.
 */
import Icon from "@/components/Icon";
import { accentForSlot } from "@/lib/categorical";
import { CATEGORY_LABEL } from "@/lib/statusCategory";
import { statusAccent } from "@/lib/statusAccent";

import type { NodeSummary, SummaryChild } from "../lib/api";
import { spaceMarker } from "../lib/tree";
import { Stat } from "./AnalyticsPanels";

/** The lanes as table columns, board order. Cancelled earns no column of
 *  its own until somebody cancels something. See `columns()` below. */
const COLUMN_ORDER = [
  "triage",
  "backlog",
  "todo",
  "in_progress",
  "done",
  "cancelled",
] as const;

/** The labels are `lib/statusCategory.ts`'s. One place names a category. */
const COLUMN_LABELS: Record<string, string> = CATEGORY_LABEL;

/**
 * Which category columns the table draws: every ordered lane with at least
 * one task anywhere, plus any category the client has not learned. An
 * all-zero column is noise. A dropped non-zero one would make the row sums
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

export default function SpaceSummary({
  summary,
  onOpen,
}: {
  /** `GET /projects/summary`, or `GET /projects/nodes/{id}/summary` for a node. */
  summary: NodeSummary;
  /** Open a child: a space, a folder or a project. */
  onOpen: (id: string) => void;
}) {
  /**
   * ⚠️ Read the roll-up defensively, for the same reason `Stat` does.
   *
   * `children` and `by_category` are typed as present and are not guaranteed
   * to be. A response without `children` threw on `.length` before a single
   * tile rendered. `hasChildren` keeps the DIFFERENCE that the empty state
   * depends on: absent means the server did not say, and `[]` means it said
   * none.
   */
  const hasChildren = Array.isArray(summary.children);
  const children = hasChildren ? summary.children : [];
  const by = summary.by_category ?? {};
  const cats = columns({ ...summary, children, by_category: by });
  const inProgress = by.in_progress ?? 0;
  const portfolio = summary.level === "portfolio";
  // A node's own tasks sit in no child row. Without this row the rows would
  // not add up to the footer (the `NodeDashboard` OwnRow reason).
  const own = !portfolio && summary.own && summary.own.tasks > 0 ? summary.own : null;

  return (
    <div className="space-y-3">
      {/* The KPI strip: Plane's total-insights row, our Stat idiom. Four
          tiles for the whole organization divide by 2 and by 4, so no tile
          sits alone on a row. */}
      <div
        className={`grid gap-2 ${portfolio ? "grid-cols-2 sm:grid-cols-4" : "grid-cols-3"}`}
      >
        {portfolio && (
          <Stat label="Spaces" value={hasChildren ? children.length : undefined} />
        )}
        <Stat label="Projects" value={summary.projects} />
        <Stat label="Tasks" value={summary.tasks} />
        <Stat label="In progress" value={inProgress} />
      </div>

      {children.length > 0 || own ? (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border bg-card text-left">
                <th className="px-3 py-2 font-medium text-muted-foreground">
                  {portfolio ? "Space" : "Under it"}
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
              {own && (
                <CountRow
                  name="Direct work"
                  icon="CornerDownRight"
                  iconClass="text-muted-foreground"
                  counts={own.by_category}
                  overdue={own.overdue}
                  tasks={own.tasks}
                  cats={cats}
                  title={`${own.tasks} tasks sit directly on ${summary.name}, in no subproject.`}
                />
              )}
              {children.map((child) => (
                <SpaceRow key={child.id} child={child} cats={cats} onOpen={onOpen} />
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border bg-card font-medium">
                <td className="px-3 py-2">{portfolio ? "All spaces" : "All"}</td>
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
        /* ⚠️ Two different facts, two different sentences. An empty state is
           a claim about the world, and a false one sends the reader to create
           what they already have. `children` absent means the server did not
           tell us. An empty array means it did, and the answer was none. */
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
          {!hasChildren
            ? "Could not load the roll-up. The spaces in the rail are unaffected."
            : portfolio
              ? "No spaces yet. Create one with the + beside Spaces."
              : "Nothing sits under this node yet."}
        </p>
      )}
    </div>
  );
}

/** The cells of one row: a count for each column, Overdue and Total. */
function Counts({
  name,
  counts,
  overdue,
  tasks,
  cats,
}: {
  name: string;
  counts: Record<string, number> | undefined;
  overdue: number;
  tasks: number;
  cats: string[];
}) {
  return (
    <>
      {cats.map((cat) => {
        const count = (counts ?? {})[cat] ?? 0;
        return (
          <td
            key={cat}
            className={`px-3 py-2 text-right ${count === 0 ? "text-muted-foreground/50" : "text-foreground"}`}
            // A wide grid puts many columns between a cell and its heading, so
            // the cell restates both halves rather than making the reader
            // track back along the row.
            title={`${name}: ${count} task${count === 1 ? "" : "s"} in ${
              COLUMN_LABELS[cat] ?? cat
            }`}
          >
            {count}
          </td>
        );
      })}
      <td
        className={`px-3 py-2 text-right ${
          overdue > 0 ? statusAccent({ category: "cancelled" }).text : "text-muted-foreground/50"
        }`}
      >
        {overdue}
      </td>
      <td className="px-3 py-2 text-right text-foreground">{tasks}</td>
    </>
  );
}

/**
 * The node's own row. Not a control: it is already where the reader is, so
 * a click would do nothing, and a row that looks like its neighbours and
 * acts otherwise is worse than a row that plainly is not a control.
 */
function CountRow({
  name,
  icon,
  iconClass,
  counts,
  overdue,
  tasks,
  cats,
  title,
}: {
  name: string;
  icon: string;
  iconClass: string;
  counts: Record<string, number> | undefined;
  overdue: number;
  tasks: number;
  cats: string[];
  title: string;
}) {
  return (
    <tr className="border-b border-dashed border-border" title={title}>
      <td className="px-3 py-2">
        <span className="flex min-w-0 items-center gap-2">
          <Icon name={icon} className={`h-3.5 w-3.5 shrink-0 ${iconClass}`} />
          <span className="truncate font-medium text-muted-foreground">{name}</span>
        </span>
      </td>
      <Counts name={name} counts={counts} overdue={overdue} tasks={tasks} cats={cats} />
    </tr>
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
  // The space's own marker, exactly as the sidebar draws it. Plane's
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
      <Counts
        name={child.name}
        counts={child.by_category}
        overdue={child.overdue}
        tasks={child.tasks}
        cats={cats}
      />
    </tr>
  );
}
