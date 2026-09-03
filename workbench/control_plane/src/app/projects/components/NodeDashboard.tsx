"use client";

/**
 * The roll-up a SPACE or a FOLDER shows instead of a board — and, since the
 * Overview canvas (owner ask 2026-08-31), the same dashboard a PROJECT can
 * choose beside its task views.
 *
 * Owner directive 2026-08-31: *"top-level spaces should not appear as
 * projects. Instead, they will display a dashboard summarizing all projects
 * contained within the spaces, without the additional views available to
 * regular projects."* A folder does the same for the projects beneath it.
 *
 * The layout is patterned on Plane (github.com/makeplane/plane @ effd0c5):
 * a KPI strip on top (`analytics/insight-card.tsx` — label over number),
 * then children beside a Progress card
 * (`cycles/active-cycle/progress.tsx` — "N/M closed", a single bar, and a
 * per-lane legend with a cancelled footnote), with each child carrying a
 * completion figure (`analytics/overview/active-project-item.tsx`). Colours
 * and controls stay OURS: every hue reaches the screen through
 * `statusAccent`, never a hex value.
 *
 * ⚠️ **It reads ONE endpoint** (`GET /nodes/{id}/summary`), which counts the
 * whole subtree in two grouped queries. Fetching each descendant's tasks
 * here would be N+1 across a real workspace, and the numbers would drift
 * while the component walked — a dashboard that disagrees with itself is
 * worse than no dashboard.
 *
 * ⚠️ **Every count is the CALLER'S.** The server filters through the same
 * task-visibility clause the list uses, so a member who can reach one
 * project inside a space sees totals matching what they could reach by
 * clicking. A roll-up that summed rows the reader cannot open would be a
 * disclosure channel wearing a summary's clothes.
 */
import Icon from "@/components/Icon";
import { statusAccent } from "@/lib/statusAccent";

import type { NodeSummary, SummaryChild } from "../lib/api";
import { nodeKind } from "../lib/tree";

/** The order lanes read in — the same left-to-right a board uses. */
const CATEGORY_ORDER = [
  "triage",
  "backlog",
  "todo",
  "in_progress",
  "done",
  "cancelled",
] as const;

const CATEGORY_LABELS: Record<string, string> = {
  triage: "Triage",
  backlog: "Backlog",
  todo: "To do",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};

function orderedCategories(counts: Record<string, number>) {
  const known = CATEGORY_ORDER.filter((c) => (counts[c] ?? 0) > 0);
  // A category the client has not learned still shows, after the known
  // ones. Dropping it would make the bars disagree with the total.
  const extra = Object.keys(counts)
    .filter((c) => !CATEGORY_ORDER.includes(c as never) && counts[c] > 0)
    .sort();
  return [...known, ...extra];
}

/**
 * One figure, and never a blank. Same contract as `AnalyticsView`'s Stat.
 *
 * ⚠️ `value` is typed as a number and is not guaranteed to be one. The roll-up
 * is cast rather than validated, so an absent `projects` renders `{undefined}`
 * — which is NOTHING. A heading over empty space reads as a number that
 * failed, not as a number that is zero, and the reader cannot tell which.
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

/**
 * One row's task mix as a single proportional bar.
 *
 * A bar rather than a number per lane: the question a roll-up answers is
 * "where is this concentrated", and six numbers make a reader do the
 * division themselves. Each segment carries its lane's SEMANTIC hue through
 * `statusAccent` — the same vocabulary the board draws — so a lane is the
 * same colour in both places (AGENTS.md rule 5).
 */
function MixBar({ counts, total }: { counts: Record<string, number>; total: number }) {
  if (total === 0) {
    return (
      <div className="h-1.5 w-full rounded-full bg-muted" aria-hidden="true" />
    );
  }
  const categories = orderedCategories(counts);
  return (
    <div
      className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted"
      role="img"
      aria-label={categories
        .map((c) => `${CATEGORY_LABELS[c] ?? c}: ${counts[c]}`)
        .join(", ")}
    >
      {categories.map((category) => (
        <span
          key={category}
          className={statusAccent({ category }).dot}
          style={{ width: `${(counts[category] / total) * 100}%` }}
        />
      ))}
    </div>
  );
}

/**
 * A child's completion, as Plane's active-projects list prints it — one
 * percentage, not another bar. NOT Plane's red-under-51%: a fresh project
 * at 0% is not failing, and painting it the cancelled hue would say it is.
 * Done wears the done tone only when everything closable is closed.
 */
function CompletionFigure({ child }: { child: SummaryChild }) {
  const closable = child.tasks - (child.by_category.cancelled ?? 0);
  if (closable <= 0) return null;
  const doneCount = child.by_category.done ?? 0;
  const pct = Math.round((doneCount / closable) * 100);
  return (
    <span
      className={`shrink-0 text-xs ${
        pct >= 100
          ? statusAccent({ category: "done" }).text
          : "text-muted-foreground"
      }`}
    >
      {pct}%
    </span>
  );
}

function ChildRow({
  child,
  level,
  onOpen,
}: {
  child: SummaryChild;
  /** The PARENT's level — it decides what a child row is called. */
  level: NodeSummary["level"];
  onOpen: (id: string) => void;
}) {
  const isFolder = nodeKind(child) === "folder";
  const glyph = isFolder
    ? "Folder"
    : level === "portfolio"
      ? "Boxes"
      : level === "project" || level === "subproject"
        ? "GitBranch"
        : "Kanban";
  return (
    <button
      type="button"
      onClick={() => onOpen(child.id)}
      className="flex w-full flex-col gap-1.5 rounded-lg border border-border bg-card px-3 py-2.5 text-left tech-transition hover:bg-muted"
    >
      <div className="flex min-w-0 items-center gap-2">
        <Icon
          name={glyph}
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
        />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {child.name}
        </span>
        {child.overdue > 0 ? (
          <span className={`shrink-0 text-xs ${statusAccent({ category: "cancelled" }).text}`}>
            {child.overdue} late
          </span>
        ) : null}
        <CompletionFigure child={child} />
        <span className="shrink-0 text-xs text-muted-foreground">
          {child.tasks} {child.tasks === 1 ? "task" : "tasks"}
        </span>
      </div>
      <MixBar counts={child.by_category} total={child.tasks} />
    </button>
  );
}

const LEVEL_TITLES: Record<NodeSummary["level"], string> = {
  portfolio: "Every space",
  space: "Space overview",
  folder: "Folder overview",
  project: "Project overview",
  subproject: "Subproject overview",
};

const EMPTY_COPY: Record<NodeSummary["level"], string> = {
  portfolio: "No spaces yet. Create one with the + beside Spaces.",
  space:
    "This space is empty. Add a project or a folder with the + on its row.",
  folder: "This folder is empty. Add a project with the + on its row.",
  project:
    "No subprojects. The task views hold everything this project owns.",
  subproject:
    "Nothing sits below a subproject — the tree stops here by design.",
};

/**
 * Progress, the way Plane's active-cycle card states it: what fraction of
 * the CLOSABLE work is closed, then a per-lane tally. Cancelled work is
 * excluded from the denominator and the exclusion is written out — a
 * footnote beats a number that quietly disagrees with the lane sums.
 */
function ProgressCard({ summary }: { summary: NodeSummary }) {
  const counts = summary.by_category ?? {};
  const done = counts.done ?? 0;
  const cancelled = counts.cancelled ?? 0;
  const closable = (summary.tasks ?? 0) - cancelled;
  const pct = closable > 0 ? (done / closable) * 100 : 0;

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
          Progress
        </p>
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{done}</span>
          {`/${closable} closed`}
        </p>
      </div>
      <div
        className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={`${Math.round(pct)}% of closable tasks are done`}
      >
        <span
          className={`block h-full ${statusAccent({ category: "done" }).dot}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="space-y-1.5">
        {orderedCategories(counts).map((category) => (
          <div
            key={category}
            className="flex items-center justify-between text-xs"
          >
            <span className="flex items-center gap-1.5 text-muted-foreground">
              <span
                className={`h-2 w-2 rounded-full ${statusAccent({ category }).dot}`}
              />
              {CATEGORY_LABELS[category] ?? category}
            </span>
            <span className="text-foreground">
              {counts[category] ?? 0}
            </span>
          </div>
        ))}
      </div>
      {cancelled > 0 ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {cancelled} cancelled — excluded from the count above.
        </p>
      ) : null}
    </div>
  );
}

export default function NodeDashboard({
  summary,
  onOpen,
}: {
  summary: NodeSummary;
  /** Drill into a child. The tree selection and this view stay in step. */
  onOpen: (id: string) => void;
}) {
  const level = summary.level;
  // ⚠️ `by_category` is typed as present and is not guaranteed to be. A summary
  // without it threw here and took the pane with it (measured 2026-09-03).
  // Every read below already handles a missing COUNT, so an empty object is the
  // shape the rest of this function was written for.
  const by = summary.by_category ?? {};
  /**
   * ⚠️ Every field below is TYPED as present and none is guaranteed to be.
   * `api.call` casts the roll-up rather than validating it, so one absent key
   * threw and took the whole pane with it. `hasChildren` keeps the difference
   * the empty state needs: absent means the server did not say, `[]` means it
   * said none.
   */
  const hasChildren = Array.isArray(summary.children);
  const children = hasChildren ? summary.children : [];
  const done = by.done ?? 0;
  const inProgress = by.in_progress ?? 0;
  // "To do" in the wide sense a reader means it: everything not yet started.
  const todo = (by.todo ?? 0) + (by.backlog ?? 0) + (by.triage ?? 0);

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <p className="mb-3 text-[11px] uppercase tracking-wider text-muted-foreground">
        {LEVEL_TITLES[level]}
      </p>

      {/* ⚠️ FIVE tiles, and 5 divides by neither 2 nor 3. So the last one sat
          alone on its own row at phone and tablet width — an orphan, which
          reads as a tile that failed to load rather than as the fifth of five.
          It spans the leftover columns instead, until all five fit in one row. */}
      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <Stat
          label={
            level === "portfolio"
              ? "Spaces"
              : level === "project" || level === "subproject"
                ? "Subprojects"
                : "Projects"
          }
          value={
            level === "portfolio"
              ? // `children` is typed as present and is not guaranteed to be.
                // Absent means the server did not say, which is a dash, not 0.
                (hasChildren ? children.length : undefined)
              : summary.projects
          }
        />
        <Stat label="To do" value={todo} />
        <Stat label="In progress" value={inProgress} />
        <Stat label="Done" value={done} />
        <Stat
          label="Overdue"
          value={summary.overdue}
          // The fifth of five: fills the leftover column at 2-up rather than
          // sitting alone on a row of its own.
          className="col-span-2 sm:col-span-1"
          tone={
            summary.overdue > 0
              ? statusAccent({ category: "cancelled" }).text
              : undefined
          }
        />
      </div>

      <div className="grid grid-cols-1 items-start gap-4 md:grid-cols-5">
        <div className="md:col-span-3">
          {children.length > 0 ? (
            <div className="space-y-1.5">
              {children.map((child) => (
                <ChildRow
                  key={child.id}
                  child={child}
                  level={level}
                  onOpen={onOpen}
                />
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
              {/* ⚠️ A level this map does not know rendered an EMPTY dashed
                  box — a container with nothing in it, which reads as a
                  half-built feature. The fallback is deliberately vague
                  because the honest answer is that we do not know. */}
              {EMPTY_COPY[level] ?? "Nothing to show here."}
            </p>
          )}
        </div>
        <div className="md:col-span-2">
          {(summary.tasks ?? 0) > 0 ? <ProgressCard summary={summary} /> : null}
        </div>
      </div>
    </div>
  );
}
