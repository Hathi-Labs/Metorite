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
import { landingLane } from "./statusOrder";

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

  // ⚠️ The STAGE axis is never POSITIONAL, and that half has not changed.
  //
  // Measured on a real board 2026-09-16, before this arm existed: the stage
  // columns fell through to positional hues and came out Backlog grey, To do
  // BLUE, In progress VIOLET, Done YELLOW — so a Done column wore the hue
  // /tasks uses for waiting, and a stage changed colour whenever a project
  // added a lane before it. A colour that moves when an unrelated lane appears
  // is not a colour anybody can learn.
  //
  // What DID change is where the meaning comes from. §9.12.3 said
  // `CATEGORY_HUES`, and that is now the fallback rather than the answer —
  // see the block below. `index`/`total` still ride along so an unknown stage
  // reaches a positional hue instead of flat gray, which is the precedence
  // `statusAccent`'s own docstring describes.
  if (groupBy === "category") {
    // ⚠️ **The stage wears its LANDING LANE's colour, and no new storage was
    // needed to say so.**
    //
    // The owner asked for stage colours that a member can configure, which are
    // per project, and which inherit exactly as statuses do. I planned a column
    // to hold them and then found the data already there: `pm_task_statuses`
    // has carried `color` since migration 146, the status editor already lets
    // anybody set it, a copy carries it (`s.color` in both copy sites), and
    // inheritance is free because a lane IS the set.
    //
    // A second place to set a stage's colour would have been a second thing to
    // keep in agreement with the first — and the first is the one the cards
    // already wear. Deriving means the column and the cards under it can never
    // disagree.
    //
    // `landingLane` is the SAME lane a drop on this column lands in, so the
    // colour is a promise about where the card goes rather than a decoration.
    //
    // A stage with no lane falls back to `CATEGORY_HUES` — the shared
    // vocabulary, which is what keeps a stage legible where no set applies.
    const lane = landingLane(statuses, groupKey);
    return lane
      ? accentForStatus(lane, index, total)
      : statusAccent({ category: groupKey, index, total });
  }

  return statusAccent({ index, total });
}

// `accentForDisposition` (S4, the My Work disposition lanes) left with the
// My work surface on 2026-08-31 — /tasks colours its own lanes through
// `app/tasks/lib/stageColors.ts`.
