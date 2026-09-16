/**
 * Projects · which facts this app hands the shared colour vocabulary (WS-27ad).
 *
 * `src/lib/statusAccent.ts` owns the palette and the precedence. This owns the
 * translation from a Projects thing — a status row, a board column on whatever
 * axis the view is grouped by — into the input that module reads.
 *
 * It is a separate file rather than three inline calls because the interesting
 * decision is *which axes carry meaning*. Only the status axis has a stored
 * colour and a machine-readable category; every other axis (assignee, project,
 * tag, importance) is a bag of values whose colour can only ever be positional,
 * and pretending otherwise — reading a keyword out of a person's name — is how
 * a board decides that "Mark Green" is a done lane.
 */

import { statusAccent, type StatusAccent } from "@/lib/statusAccent";

import type { StatusRow } from "./api";
import type { GroupBy } from "./grouping";

/**
 * The accent for one status.
 *
 * `color` is the owner's own choice (`pm_task_statuses.color`, stored since
 * migration 146 and — until this ticket — rendered nowhere), `category` is the
 * machine-readable fallback, and the name is not consulted at all: a status
 * that HAS a category never needs guessing at, and a Projects lane called
 * "Waiting on legal" is not automatically amber the way a /tasks stage is.
 */
export function accentForStatus(
  status: Pick<StatusRow, "color" | "category"> | undefined | null,
  index = 0,
  total = 0
): StatusAccent {
  return statusAccent({
    color: status?.color,
    category: status?.category,
    index,
    total,
  });
}

/**
 * The accent for a board column / list section on any axis.
 *
 * On the status axis the column key IS a status id, so the real row is looked
 * up and its colour and category answer. Off the status axis nothing
 * meaningful is known, so the hue is purely positional — neighbouring lanes
 * differ, and nothing pretends to mean anything.
 */
export function accentForGroup(
  groupBy: GroupBy,
  groupKey: string,
  index: number,
  total: number,
  statuses: readonly StatusRow[]
): StatusAccent {
  if (groupBy === "status") {
    const status = statuses.find((row) => row.id === groupKey);
    // A key with no matching row is the UNSET lane (a task with no status) or
    // a status deleted since the page loaded. Positional is the honest answer;
    // inventing a colour for "no status" would make the empty case look
    // deliberate.
    if (status) return accentForStatus(status, index, total);
  }

  // ⚠️ The STAGE axis paints from the CATEGORY, never from the column's index.
  //
  // Measured on a real board 2026-09-16, before this arm existed: the stage
  // columns fell through to positional hues and came out Backlog grey, To do
  // BLUE, In progress VIOLET, Done YELLOW. `CATEGORY_HUES` says gray, gray,
  // blue, green. A Done column was wearing the hue /tasks uses for waiting,
  // and a stage would change colour whenever a project added a lane before it.
  //
  // A stage is the one axis whose meaning is fixed across every project, which
  // makes it the one axis that must NOT be positional: the same word has to be
  // the same colour in two different spaces, or the shared vocabulary stops
  // looking shared. §9.12.3, "Colour comes from CATEGORY_HUES. No new palette."
  //
  // `index`/`total` ride along so an UNKNOWN stage — a server ahead of this
  // client — falls through to a positional hue rather than to flat gray.
  // `statusAccent` tries category first and only then the positional step,
  // which is the precedence its own docstring describes.
  if (groupBy === "category") {
    return statusAccent({ category: groupKey, index, total });
  }

  return statusAccent({ index, total });
}

// `accentForDisposition` (S4, the My Work disposition lanes) left with the
// My work surface on 2026-08-31 — /tasks colours its own lanes through
// `app/tasks/lib/stageColors.ts`.
