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
import type { GtdItem } from "./types";

/**
 * Whether "Move to project…" is offered on a card — ONE rule, one spelling.
 *
 * Lens only: `apiMoveTask` throws when the flag is off (the old store has no
 * board to move onto), and a door that opens onto an error is worse than no
 * door. An archived row stays where it is until it is restored.
 */
export function promoteAllowed(
  item: Pick<GtdItem, "archivedAt">,
  lens: boolean,
): boolean {
  return lens && !item.archivedAt;
}

/**
 * What a promote left behind in MY list.
 *
 * `left: false` is the ordinary case: the task is still mine, re-read through
 * the lens. `left: true` is a promote that handed the task to a colleague, or
 * from which I had already removed myself: `my/tasks/{id}` answers 404
 * because the row is no longer in my membership, and that is a SUCCESS the
 * store must not report as a failure.
 */
export type PromoteOutcome =
  | { left: false; item: GtdItem }
  | { left: true; projectId?: string; assignees: string[] };

/** The success toast, in one place so the two outcomes cannot drift. */
export function promoteToast(
  outcome: PromoteOutcome,
  projectName: string,
  dropped: readonly string[] | null,
): { title: string; description: string } {
  const drops =
    dropped && dropped.length > 0
      ? ` Dropped ${dropped.join(", ")}: the old values are on the timeline.`
      : "";
  if (outcome.left) {
    const who = outcome.assignees.length > 0 ? outcome.assignees.join(", ") : "nobody";
    return {
      title: `Moved to ${projectName}, handed to ${who}.`,
      description: `It left your list. It is on the board now.${drops}`,
    };
  }
  return {
    title: `Moved to ${projectName}`,
    description: `It is the same task, now on the board. Completing it there completes it here.${drops}`,
  };
}

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
