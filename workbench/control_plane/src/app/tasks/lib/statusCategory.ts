/**
 * My Tasks · Next Actions grouped by the Projects status CATEGORY (D73.9).
 *
 * Owner request, 2026-09-23: with no ClickUp connection, the status mapping
 * leaves My Tasks settings, and the stages come from Projects. A lane NAME
 * belongs to one project ("Building" in one, "In progress" in another). A lane
 * CATEGORY is the shared vocabulary every project's lanes carry
 * (`pm_task_statuses.category`). So Next Actions groups by the category, and
 * each card keeps its own lane name as its pill.
 *
 * Two pure rules live here, so the list, the board, the pill and the context
 * menu read one spelling. `statusCategory.test.ts` fences both.
 */

import type { LensLane } from "./lens";
import type { GtdItem } from "./types";

/** The three groups of Next Actions, in order. */
export type NextCategory = "todo" | "in_progress" | "done";

export const NEXT_CATEGORIES: readonly NextCategory[] = ["todo", "in_progress", "done"];

export const CATEGORY_LABEL: Readonly<Record<NextCategory, string>> = {
  todo: "To do",
  in_progress: "In progress",
  done: "Done",
};

/** Is `v` one of the three Next Actions groups? */
export function isNextCategory(v: string | undefined | null): v is NextCategory {
  return v === "todo" || v === "in_progress" || v === "done";
}

/**
 * The Next Actions group one task sits in, or null for a task that does not
 * belong in Next.
 *
 * * A task I marked done is Done, whatever its lane says.
 * * `todo`, `in_progress` and `done` are the three groups.
 * * `backlog` is Someday (the derived disposition), and `triage` and
 *   `cancelled` are not next actions, so all three answer null.
 * * A row with no category (the demo backend's mock rows) is To do. It is the
 *   first group, the one a fresh next action lands in.
 */
export function nextCategoryOf(
  item: Pick<GtdItem, "statusCategory" | "disposition">,
): NextCategory | null {
  if (item.disposition === "DONE") return "done";
  const c = item.statusCategory;
  if (!c) return "todo";
  return isNextCategory(c) ? c : null;
}

/**
 * The lane a task moves to when it is dragged into `category`: the FIRST lane
 * by position with that category, among the lanes of the task's own project
 * (`GET /projects/my/tasks/{id}/lanes`, S6e). Undefined when the project has
 * no lane of that category. The caller then says so, and moves nothing.
 */
export function laneForCategory(
  lanes: readonly LensLane[],
  category: NextCategory,
): LensLane | undefined {
  return [...lanes]
    .filter((l) => l.category === category)
    .sort((a, b) => a.position - b.position)[0];
}

/** The toast for a project with no lane of the category. */
export function noLaneMessage(category: NextCategory, projectName?: string): string {
  const where = projectName ? `"${projectName}"` : "this task's project";
  return (
    `${where} has no ${CATEGORY_LABEL[category]} lane, so the task stays where ` +
    "it is. Add one in Projects."
  );
}
