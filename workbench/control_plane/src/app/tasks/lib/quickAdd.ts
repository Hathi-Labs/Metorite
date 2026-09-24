// /tasks · group-context quick-add (the WS-27y pattern, backported).
//
// A quick-add lives inside a group — a board column, a grouped-list section —
// and the task it creates must LAND in that group, or the add reads as a
// failure while the task sits in some default bucket off-screen. The mapping
// from "where the input is" to "what the created item must carry" is this
// module, pure and surface-agnostic, exactly like `app/projects/lib/quickAdd`
// on the other side of the wall: the payloads differ (`gtd_items` speaks
// workflowStage/context/energy, `pm_tasks` speaks status_id/tags), the
// grammar is the same.
//
// One divergence from the Projects mapping, and it is deliberate: /tasks has
// COMPUTED axes. The priority and mode groups are projections of
// important × leveraged × urgent-from-dueAt (`priority.ts`), so no create
// payload can promise the new task lands in "Critical" — urgency needs a due
// date nobody typed. Where Projects answers an unknown axis with "a plain add,
// never an error", these axes answer `null` — the surface offers no quick-add
// there at all — because in a grouped list a plain add that visibly files
// itself into a SIBLING group is not a plain add, it is a lie about where the
// task went.

import type { GroupBy } from "./ordering";
import { NO_CONTEXT_GROUP } from "./priority";
import type { Disposition, Energy, ViewKey } from "./types";
import { type NextCategory, isNextCategory } from "./statusCategory";

/** What a quick-added task must carry to belong to its group. */
export interface QuickAddPrefill {
  /** The Next Actions group (D73.9): a status category, never a lane name. */
  statusCategory?: NextCategory;
  context?: string;
  energy?: Energy;
  deepWork?: boolean;
  /** The GTD bucket the add lands in. Absent = the default NEXT. */
  disposition?: Disposition;
}

/** The axes a /tasks quick-add can sit inside: the status axis (`""`, the
 *  grouped list's default and the board's columns) or a toolbar lens. */
export type QuickAddAxis = GroupBy | "";

const ENERGIES: ReadonlySet<string> = new Set(["low", "medium", "high"]);

/**
 * (axis, group key) → the prefill, or `null` when the group cannot honestly
 * take a quick-add (a computed axis — see the header).
 *
 * The unset buckets ("No context", "No energy set", "Shallow") ask for
 * nothing: a bare next action is already context-less, energy-less and
 * shallow, so "create it in this group" is what a bare create does.
 */
export function quickAddPrefill(
  axis: QuickAddAxis,
  key: string,
): QuickAddPrefill | null {
  switch (axis) {
    case "":
      // The status axis: the board's columns and the grouped list's
      // category sections. The key IS the category (D73.9).
      return isNextCategory(key) ? { statusCategory: key } : {};
    case "context":
      return key === NO_CONTEXT_GROUP ? {} : { context: key };
    case "energy":
      return ENERGIES.has(key) ? { energy: key as Energy } : {};
    case "depth":
      return key === "deep" ? { deepWork: true } : {};
    case "none":
      return {};
    case "priority":
    case "orgPriority":
    case "mode":
      // Computed from flags + due date — a create cannot promise the landing.
      return null;
  }
}

/**
 * WS-27ad — the quick-add for a FLAT view, whose "group" is the view itself.
 *
 * The Done / Someday / Waiting / Archive lists had no capture box at all, so
 * the only way to put something on your Someday list was to capture it into the
 * Inbox and clarify it there — two steps for a thought that was already
 * classified when you had it.
 *
 * Same null grammar as the axes above: a view that cannot honestly take the add
 * offers no box, and each refusal has a reason rather than an omission.
 */
export function viewQuickAdd(
  view: ViewKey,
): { prefill: QuickAddPrefill; label: string } | null {
  switch (view) {
    case "someday":
      // The clean case: "incubate this" is a complete decision, and Someday is
      // exactly where an incubating thought belongs.
      return { prefill: { disposition: "SOMEDAY" }, label: "Add to Someday" };
    case "done":
      // A log entry, not a to-do — the same thing the board already means when
      // you quick-add into the last stage (see `quickAddNext`). Worth having:
      // recording work that was never a task is otherwise impossible here.
      return { prefill: { disposition: "DONE" }, label: "Log something done" };
    case "waiting":
      // NO box. Waiting-For is grouped by PERSON and a waiting-for's whole
      // content is who owes it and since when; a create can set the bucket but
      // not the delegation (that is the delegate path, which asks for the
      // person and stamps `delegatedAt`). An add here would visibly file itself
      // into "Unassigned" — a sibling group — which this module's header calls
      // what it is: a lie about where the task went.
      return null;
    case "archive":
      // Creating a task in order to immediately hide it is not a gesture.
      return null;
    default:
      // Inbox has its own always-open capture row; next/priority/engage are
      // grouped surfaces served by `quickAddPrefill`; calendar days get their
      // own per-day box.
      return null;
  }
}
