/**
 * Projects · multi-select and the bulk request (WS-27n).
 *
 * §11.3 names this ticket as the one that gates the ClickUp cutover: an import
 * that cannot be re-triaged in bulk is one somebody abandons halfway, leaving
 * two live systems.
 *
 * Selection is a set of ids held by the board, and everything here is a pure
 * function over it. The awkward cases are the ones a selection UI always gets
 * wrong — shift-clicking a range, a selected card that a filter has since
 * removed from the page, and a bulk request that would say "change nothing".
 *
 * **WS-27ad — the selection GRAMMAR moved to `src/lib/selection.ts`** and is
 * re-exported below, unchanged, so every existing Projects import still works.
 * /tasks now speaks the same one: toggle, shift-range from an anchor, prune on
 * filter change, never remove on shift. What stays here is what is genuinely
 * this app's — the bulk REQUEST (`pm_tasks` fields, the gateway's no-op 422)
 * and the board's own render order.
 */

import {
  allSelected,
  clickSelect,
  prune,
  range,
  toggle,
} from "@/lib/selection";

import type { TaskRow } from "./api";
import { type MatrixFlags, flagsPatch } from "./matrix";
import { type Filters } from "./grouping";

export { allSelected, clickSelect, prune, range, toggle };
export type { SelectionState } from "@/lib/selection";

export interface BulkRequest {
  task_ids: string[];
  patch?: Record<string, unknown>;
  assignees_add?: string[];
  assignees_remove?: string[];
  tags_add?: string[];
  tags_remove?: string[];
}

/** Mirrors the gateway's `MAX_BULK`. */
export const MAX_BULK = 500;

/** Every visible id, in the board's own order. */
export function visibleIds(groups: { tasks: TaskRow[] }[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const group of groups) {
    for (const task of group.tasks) {
      // A task with two assignees is drawn in two columns (WS-27k), so the
      // same id genuinely appears twice. Selecting it twice would make the
      // count say two for one task.
      if (!seen.has(task.id)) {
        seen.add(task.id);
        out.push(task.id);
      }
    }
  }
  return out;
}

export interface BulkDraft {
  status: string;
  /** D78 — the priority flags to set on every selected task. `""` leaves them
   *  alone, `"none"` clears both, and the rest are `MatrixFlags`. */
  priority: "" | "none" | Exclude<MatrixFlags, "">;
  assigneeAdd: string;
  assigneeRemove: string;
  tagAdd: string;
  tagRemove: string;
}

export const EMPTY_DRAFT: BulkDraft = {
  status: "",
  priority: "",
  assigneeAdd: "",
  assigneeRemove: "",
  tagAdd: "",
  tagRemove: "",
};

const list = (raw: string): string[] =>
  raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

/**
 * The draft → the request body, or `null` when it asks for nothing.
 *
 * `null` rather than an empty request: the gateway answers 422 for a no-op, and
 * a button that fires a request only to be told "nothing to change" should have
 * been disabled instead.
 *
 * **Status goes by NAME.** A selection can span projects and a status id
 * belongs to exactly one root, so the id would put most of the selection in a
 * lane that is not theirs — the gateway refuses `status_id` for that reason.
 */
export function buildRequest(
  ids: readonly string[],
  draft: BulkDraft
): BulkRequest | null {
  if (ids.length === 0) return null;

  const patch: Record<string, unknown> = {};
  if (draft.status) patch.status = draft.status;
  // D78 — `""` is "leave it alone". "Not flagged" is its own value, `"none"`,
  // so an untouched box can never clear the flags of every selected task.
  if (draft.priority !== "") {
    Object.assign(patch, flagsPatch(draft.priority === "none" ? "" : draft.priority));
  }

  const request: BulkRequest = { task_ids: [...ids] };
  if (Object.keys(patch).length) request.patch = patch;

  const addPeople = list(draft.assigneeAdd);
  const dropPeople = list(draft.assigneeRemove);
  const addTags = list(draft.tagAdd);
  const dropTags = list(draft.tagRemove);
  if (addPeople.length) request.assignees_add = addPeople;
  if (dropPeople.length) request.assignees_remove = dropPeople;
  if (addTags.length) request.tags_add = addTags;
  if (dropTags.length) request.tags_remove = dropTags;

  const asks =
    Boolean(request.patch) ||
    addPeople.length + dropPeople.length + addTags.length + dropTags.length > 0;
  return asks ? request : null;
}

export interface BulkOutcome {
  requested: number;
  applied: number;
  skipped: { task_id: string; reason: string }[];
  failed: { task_id: string; reason: string }[];
}

/**
 * What to tell somebody after a bulk edit.
 *
 * Every category is named, including the boring ones. A sweep that reports only
 * its successes is how "it said 47 changed" and "but I selected 50" become a
 * support conversation instead of a sentence somebody already read.
 */
export function describeOutcome(outcome: BulkOutcome): string {
  const parts = [`${outcome.applied} of ${outcome.requested} updated`];

  const unchanged = outcome.skipped.filter((s) => s.reason === "unchanged").length;
  if (unchanged) parts.push(`${unchanged} already like that`);

  const missing = outcome.skipped.filter((s) => s.reason === "not_found").length;
  // Deliberately vague about WHY: the API answers 404 for "not yours" as well
  // as "no such thing" (R5), and the UI must not invent a distinction the
  // server refuses to make.
  if (missing) parts.push(`${missing} not available`);

  if (outcome.failed.length) parts.push(`${outcome.failed.length} failed`);
  return `${parts.join(", ")}.`;
}

/** Filters that would make the current selection meaningless if re-applied. */
export const selectionSurvives = (before: Filters, after: Filters): boolean =>
  JSON.stringify(before) === JSON.stringify(after);
