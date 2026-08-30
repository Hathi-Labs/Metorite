"use client";

/**
 * The roll-up a SPACE or a FOLDER shows instead of a board.
 *
 * Owner directive 2026-08-31: *"top-level spaces should not appear as
 * projects. Instead, they will display a dashboard summarizing all projects
 * contained within the spaces, without the additional views available to
 * regular projects."* A folder does the same for the projects beneath it.
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

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className={`text-lg font-semibold ${tone ?? "text-foreground"}`}>
        {value}
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
  return (
    <button
      type="button"
      onClick={() => onOpen(child.id)}
      className="flex w-full flex-col gap-1.5 rounded-lg border border-border bg-card px-3 py-2.5 text-left tech-transition hover:bg-muted"
    >
      <div className="flex min-w-0 items-center gap-2">
        <Icon
          name={isFolder ? "Folder" : level === "portfolio" ? "Boxes" : "Kanban"}
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
        <span className="shrink-0 text-xs text-muted-foreground">
          {child.tasks} {child.tasks === 1 ? "task" : "tasks"}
        </span>
      </div>
      <MixBar counts={child.by_category} total={child.tasks} />
    </button>
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
  const done = summary.by_category.done ?? 0;
  const open = summary.tasks - done - (summary.by_category.cancelled ?? 0);

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <p className="mb-3 text-[11px] uppercase tracking-wider text-muted-foreground">
        {level === "portfolio"
          ? "Every space"
          : level === "space"
            ? "Space overview"
            : "Folder overview"}
      </p>

      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat
          label={level === "portfolio" ? "Spaces" : "Projects"}
          value={
            level === "portfolio" ? summary.children.length : summary.projects
          }
        />
        <Stat label="Open" value={open} />
        <Stat label="Done" value={done} />
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

      {summary.tasks > 0 ? (
        <div className="mb-5">
          <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
            {orderedCategories(summary.by_category).map((category) => (
              <span
                key={category}
                className="flex items-center gap-1.5 text-xs text-muted-foreground"
              >
                <span
                  className={`h-2 w-2 rounded-full ${statusAccent({ category }).dot}`}
                />
                {CATEGORY_LABELS[category] ?? category}
                <span className="text-foreground">
                  {summary.by_category[category]}
                </span>
              </span>
            ))}
          </div>
          <MixBar counts={summary.by_category} total={summary.tasks} />
        </div>
      ) : null}

      {summary.children.length > 0 ? (
        <div className="space-y-1.5">
          {summary.children.map((child) => (
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
          {level === "portfolio"
            ? "No spaces yet. Create one with the + beside Spaces."
            : level === "space"
              ? "This space is empty. Add a project or a folder with the + on its row."
              : "This folder is empty. Add a project with the + on its row."}
        </p>
      )}
    </div>
  );
}
