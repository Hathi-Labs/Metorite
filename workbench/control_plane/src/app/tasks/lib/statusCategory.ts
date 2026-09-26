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

import { CATEGORY_LABEL } from "@/lib/statusCategory";

import type { MyTask } from "./types";

/** The three groups of Next Actions, in order. */
export type NextCategory = "todo" | "in_progress" | "done";

export const NEXT_CATEGORIES: readonly NextCategory[] = ["todo", "in_progress", "done"];

// ⚠️ No label table here. The labels are `lib/statusCategory.ts`'s
// `CATEGORY_LABEL`, the one place a category is named, so a Projects stage
// and a My Tasks group read the same word. This file held its own copy until
// 2026-09-24. Fence: `sharedTaskUi.test.ts`, "the stage labels".

/** Is `v` one of the three Next Actions groups? */
export function isNextCategory(v: string | undefined | null): v is NextCategory {
  return v === "todo" || v === "in_progress" || v === "done";
}

/**
 * The Next Actions group one task sits in, or null for a task that does not
 * belong in Next.
 *
 * `itemsForView("next")` already chose the rows: a task I stated or derived as
 * NEXT, or DONE. This only says which header it sits under.
 *
 * * `cancelled` is not a next action, and answers null. It is tested FIRST
 *   (D79): the gateway's `effective_disposition` turns every closing lane,
 *   Cancelled too, into DONE, so a test on DONE first drew every cancelled
 *   task under Done (§4.9 point 2).
 * * A task I marked done is Done, whatever its lane says.
 * * `in_progress` and `done` are their own groups.
 * * `todo`, `backlog` and `triage` sit under To do. "backlog is Someday" is
 *   the derivation for a task with NO stated disposition. Once I state NEXT,
 *   the lane does not hide it. The S3b backfill put every moved task in its
 *   root's Inbox lane (category `backlog`), and an organize to Next writes
 *   only the overlay, so both land here (§4.9 point 5).
 * * A row with no category (the demo backend's mock rows) is To do.
 */
export function nextCategoryOf(
  item: Pick<MyTask, "statusCategory" | "disposition">,
): NextCategory | null {
  const c = item.statusCategory;
  if (c === "cancelled") return null;
  if (item.disposition === "DONE") return "done";
  if (c === "in_progress" || c === "done") return c;
  return "todo";
}

// ⚠️ No lane resolver here. Which status a stage resolves to is
// `landingLane` / `needsChoice` in `@/lib/statusCategory`, the one rule both
// apps read (D79). This file held its own first-by-position copy
// (`laneForCategory`) until 2026-09-25. Fence: `statusCategory.test.ts`.

/** The toast for a project with no lane of the category. */
export function noLaneMessage(category: NextCategory, projectName?: string): string {
  const where = projectName ? `"${projectName}"` : "this task's project";
  return (
    `${where} has no ${CATEGORY_LABEL[category]} lane, so the task stays where ` +
    "it is. Add one in Projects."
  );
}
