/**
 * Which rows a node's dashboard draws, and in what order.
 *
 * ⚠️ **This file exists because of one question the owner asked on
 * 2026-09-17:** *"If a project has subprojects, and the project itself has
 * tasks in addition to subprojects, need to consider exactly how best to
 * display this."*
 *
 * The answer, and the reasoning that picked it:
 *
 * **The node's own work is a ROW, first, and it is not a child.** The KPI
 * strip counts the whole subtree. If the rows beneath it do not add up to
 * that number, the reader either does not check — and carries away a wrong
 * idea of where the work is — or checks, and cannot find the discrepancy,
 * because every individual figure on the page is correct. Before this, a
 * project with 12 of its own tasks and 13 below it showed a strip saying 25
 * over rows saying 13.
 *
 * **First, not sorted among the children.** It is the node itself. A reader
 * scanning for "what does this project hold" should not find the project's
 * own work filed alphabetically between two subprojects.
 *
 * **Not drawn when there are no children.** A leaf project's own work IS the
 * whole dashboard, and the Progress card already states it. A "Direct work"
 * row there would be one number said twice, which teaches a reader that the
 * row means something it does not.
 *
 * **Not drawn when it is zero.** A parent that delegates everything owns no
 * work, and a row reading 0 beside real rows looks like a figure that failed
 * to load — the same confusion `Stat`'s dash exists to prevent.
 *
 * ⚠️ **Rejected: a "Direct / Everything below" toggle instead of a row.** It
 * hides the answer behind a control, so the reconciliation only happens if
 * somebody thinks to click. The failure being fixed is arithmetic that does
 * not add up on FIRST read, and a toggle does not fix a first read. The
 * scope toggle is still right for the analytics panels below, where the
 * question really is "which scope am I asking about" — that is a different
 * question from "where is this project's work".
 */
import type { NodeSummary, SummaryChild } from "./api";

/** One line in the children column. */
export type DashboardRow =
  | { kind: "child"; child: SummaryChild }
  | {
      kind: "own";
      tasks: number;
      overdue: number;
      by_category: Record<string, number>;
    };

/**
 * The rows, in draw order: the node's own work first, then its children.
 *
 * ⚠️ Every field of `summary` is TYPED as present and none is guaranteed to
 * be — `api.call` casts the response rather than validating it, and two of
 * `NodeDashboard`'s five tiles once rendered a heading over empty space for
 * exactly that reason. So this reads defensively throughout.
 */
export function dashboardRows(summary: NodeSummary): DashboardRow[] {
  const children = Array.isArray(summary.children) ? summary.children : [];
  const rows: DashboardRow[] = children.map((child) => ({
    kind: "child" as const,
    child,
  }));

  const own = summary.own;
  // Absent means the server did not say, which is not the same as zero. A
  // client running against a deployment older than the `own` block must draw
  // what it did before rather than invent a row reading 0.
  if (!own || typeof own.tasks !== "number") return rows;
  // A leaf's own work is the whole dashboard, and the Progress card says it.
  if (children.length === 0) return rows;
  // A parent that delegates everything owns nothing worth a line.
  if (own.tasks <= 0) return rows;

  return [
    {
      kind: "own",
      tasks: own.tasks,
      overdue: own.overdue ?? 0,
      by_category: own.by_category ?? {},
    },
    ...rows,
  ];
}

/**
 * Do the drawn rows account for every task the strip counts?
 *
 * The page's own arithmetic, asked out loud, and `NodeDashboard` draws a
 * footnote when the answer is no. That footnote is the point of this
 * function: the defect it was written for lost twelve tasks in silence, and
 * a page that can lose a number should be able to SAY it lost one.
 *
 * ⚠️ **It reports a mismatch on an older server too, and that is correct.**
 * A deployment with no `own` block genuinely does leave a parent's own tasks
 * out of every row. The footnote states the true count either way. Staying
 * quiet there would mean suppressing an honest number to keep a deploy
 * window tidy, which is how the original hole stayed invisible.
 */
export function rowsReconcile(summary: NodeSummary): boolean {
  const children = Array.isArray(summary.children) ? summary.children : [];
  const fromChildren = children.reduce(
    (sum, child) => sum + (child.tasks ?? 0),
    0
  );
  const own = typeof summary.own?.tasks === "number" ? summary.own.tasks : 0;
  return fromChildren + own === (summary.tasks ?? 0);
}

export const EMPTY_COPY: Record<NodeSummary["level"], string> = {
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
 * What to say when the children column has no rows.
 *
 * ⚠️ **"Empty" must never appear above a strip counting work.** A space or a
 * folder holds no tasks by design, and the API does not enforce that for a
 * space — `assert_node_grammar` refuses a task on a FOLDER and accepts one
 * on a space. So a space with tasks and no child projects said "This space
 * is empty" over a strip counting five of them (found in review,
 * 2026-09-17). Two true sentences that contradict each other teach a reader
 * to trust neither.
 *
 * A project and a subproject need no special case: their copy already says
 * the task views hold the work, which is exactly where it is.
 */
export function emptyCopy(summary: NodeSummary): string {
  const level = summary.level;
  const tasks = summary.tasks ?? 0;
  if (tasks > 0 && (level === "space" || level === "folder")) {
    const noun = level === "space" ? "space" : "folder";
    return `No projects here yet. ${tasks} ${
      tasks === 1 ? "task sits" : "tasks sit"
    } directly in this ${noun}.`;
  }
  // ⚠️ A level this map does not know rendered an EMPTY dashed box — a
  // container with nothing in it, which reads as a half-built feature. The
  // fallback is deliberately vague because the honest answer is that we do
  // not know.
  return EMPTY_COPY[level] ?? "Nothing to show here.";
}
