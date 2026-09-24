/**
 * Projects · group-context quick-add (WS-27y).
 *
 * A quick-add lives inside a group — a board column, a lane cell, a list
 * section, a calendar day — and the task it creates must LAND in that group,
 * or the add reads as a failure while the task sits in some default bucket
 * off-screen. The mapping from "where the input is" to "what the create must
 * say" is this module, pure and surface-agnostic: the board, the list, the
 * calendar, and the coming spreadsheet layout (WS-27x's bottom quick-add row)
 * all feed it an axis and a key and spread the result into their own POST.
 *
 * The plan has two parts because the API does: `create` merges into the
 * `POST /tasks` body (`TaskIn` speaks status_id, project_id, importance, tags,
 * due_at), while `assignees` is a follow-up `PUT` — assignment is not a
 * create-body field, and pretending it is would silently drop the one value
 * an assignee column's quick-add exists to set.
 */

import { UNSET, type GroupBy } from "./grouping";
import { flagsForCell, flagsPatch } from "./matrix";

/** The axes a quick-add can sit inside: any grouping, plus a calendar day. */
export type QuickAddAxis = GroupBy | "day";

export interface QuickAddPlan {
  /** Fields to merge into the `POST /tasks` body. */
  create: Record<string, unknown>;
  /** People to `PUT` onto the task once it exists. Absent when none. */
  assignees?: string[];
}

/** A plan that says nothing — the quick-add of a group with no value. */
export const EMPTY_PLAN: QuickAddPlan = { create: {} };

/**
 * A calendar day (`YYYY-MM-DD`) as a due instant: noon LOCAL time.
 *
 * Noon, not midnight — `due_at` is an instant read back in each viewer's
 * timezone (see `calendar.taskDays`), and a local-midnight instant lands on
 * yesterday for anyone west of the creator. Noon keeps the task on the
 * intended day for every viewer within ±11 hours, which is the best a single
 * instant can do for a day somebody clicked on.
 */
export function dueInstantForDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1, 12, 0, 0, 0).toISOString();
}

/**
 * (axis, group key) → what the created task must carry to belong there.
 *
 * The `UNSET` bucket asks for nothing on any axis: a task is born unassigned,
 * untagged and priority-less, so "create it in the Unassigned column" is
 * already what a bare create does. Status has no unset bucket — the server
 * assigns the project's default status when none is sent.
 */
export function quickAddPrefill(
  axis: QuickAddAxis | string,
  key: string
): QuickAddPlan {
  if (key === UNSET) return EMPTY_PLAN;
  switch (axis) {
    case "status":
      return { create: { status_id: key } };
    case "project":
      // The one axis where the fragment overrides the surface's own
      // project_id — a quick-add in the "Firmware" column creates IN Firmware.
      return { create: { project_id: key } };
    case "importance":
      // D78 — the key is a matrix level. Set the flags it implies.
      return { create: flagsPatch(flagsForCell(key)) };
    case "tag":
      return { create: { tags: [key] } };
    case "assignee":
      return { create: {}, assignees: [key] };
    case "day":
      return { create: { due_at: dueInstantForDay(key) } };
    default:
      // "none", the flat list's "all" bucket, and any axis a later view
      // invents: an add with no context is a plain add, never an error.
      return EMPTY_PLAN;
  }
}

/**
 * A lane cell is TWO contexts at once — merge their plans. Later plans win on
 * conflicting create keys (there are none between distinct axes today);
 * assignees union rather than overwrite, since both axes may name people.
 */
export function mergePlans(...plans: QuickAddPlan[]): QuickAddPlan {
  const create: Record<string, unknown> = {};
  const assignees = new Set<string>();
  for (const plan of plans) {
    Object.assign(create, plan.create);
    for (const who of plan.assignees ?? []) assignees.add(who);
  }
  return assignees.size ? { create, assignees: [...assignees] } : { create };
}
