/**
 * My Tasks · the promote door, pure half (my_tasks_cutover.md §5 S6c).
 *
 * A personal task moves onto a company board through ONE request,
 * `apiMoveTask` (D53.4: a promotion is a MOVE of `project_id`, never a copy).
 * The dialog collects three answers — where, the destination's required
 * fields, who owns it there — and this module turns them into that request,
 * or names what still blocks it.
 *
 * ⚠️ Blankness is the gateway's rule, `_is_blank`, mirrored in
 * `customFields.isBlank`: `0` and `false` are ANSWERS. The move dialog must
 * refuse the same drafts the server refuses and no others, or a member fills
 * in a zero, watches the button stay grey, and learns to distrust the form.
 */

import {
  type FieldDef,
  requiredBlanks,
  toWire,
} from "@/app/projects/lib/customFields";

import type { LensMoveRequest } from "./lens";

export interface PromoteAnswers {
  destinationId: string;
  /** The destination's REQUIRED definitions the preview named as unanswered. */
  fields: readonly FieldDef[];
  /** Control values keyed by `field_key`, as the inputs hold them. */
  draft: Record<string, unknown>;
  /** Who owns the task after the move, as the dialog shows it. */
  assignees: readonly string[];
  /** Who owned it before the dialog opened. */
  initialAssignees: readonly string[];
}

export type PromotePlan =
  | { ok: true; request: LensMoveRequest }
  | { ok: false; missing: string[] };

const sameSet = (a: readonly string[], b: readonly string[]): boolean =>
  a.length === b.length && a.every((x) => b.includes(x));

/**
 * The one request, or the names of the fields still blank.
 *
 * `assignees` travels only when the member changed it. `undefined` tells the
 * gateway to leave the owners alone, and `[]` clears them — the two are
 * different answers (`lensMoveTask`), so an untouched list must not collapse
 * into either.
 */
export function promotePlan(answers: PromoteAnswers): PromotePlan {
  const wire: Record<string, unknown> = {};
  for (const def of answers.fields) {
    wire[def.field_key] = toWire(def.field_type, answers.draft[def.field_key]);
  }
  const missing = requiredBlanks(answers.fields, wire).map((def) => def.name);
  if (missing.length > 0) return { ok: false, missing };

  const customFields: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(wire)) {
    if (value !== null && value !== undefined) customFields[key] = value;
  }
  const request: LensMoveRequest = {
    projectId: answers.destinationId,
    ...(Object.keys(customFields).length > 0 ? { customFields } : {}),
    ...(sameSet(answers.assignees, answers.initialAssignees)
      ? {}
      : { assignees: [...answers.assignees] }),
  };
  return { ok: true, request };
}
