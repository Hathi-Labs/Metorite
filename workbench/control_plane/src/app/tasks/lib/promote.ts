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
import type { MyTask } from "./types";

/**
 * Whether "Move to project…" is offered on a card — ONE rule, one spelling.
 * An archived row stays where it is until it is restored.
 */
export function promoteAllowed(item: Pick<MyTask, "archivedAt">): boolean {
  return !item.archivedAt;
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
  | { left: false; item: MyTask }
  | { left: true; projectId?: string; assignees: string[] };

/**
 * The line both promote doors show before the move (S6g): the Move dialog's
 * description and the Clarify Where hint. One sentence, one place, so the two
 * doors cannot contradict each other again. They did: the dialog said "The
 * task leaves your list" and the card said "stays yours".
 */
export { PROMOTE_HINT } from "@/app/projects/components/PromoteFields";

/**
 * How long a promote waits before it is sent (S6g). D62 refuses a move from a
 * company board back into my personal tree, so once the move commits there is
 * no Undo. The Undo is a short deferred commit instead: the request waits this
 * long, and Undo cancels it before anything reaches the server.
 */
export const PROMOTE_UNDO_MS = 5000;

/** The toast while a promote waits to be sent. */
export function promotePendingToast(projectName: string): {
  title: string;
  description: string;
} {
  return {
    title: `Moving to ${projectName}…`,
    description: "Undo stops it before anything is sent.",
  };
}

/**
 * Whether closing the tab should ask first (S6g repair P2-b). A promote that
 * is still waiting lives only in this page: close it inside the window and the
 * move is dropped, never sent. Once the request is in flight, it is the
 * server's.
 */
export function promoteBlocksUnload(
  pending: { sending: boolean } | null | undefined,
): boolean {
  return !!pending && !pending.sending;
}

/** A commit that waits, and that Undo can cancel until it runs. */
export interface DeferredCommit {
  /** Stop it. True when it had not run yet, so nothing was sent. */
  cancel(): boolean;
  /** Run it now, if it has not run. */
  flush(): void;
}

/**
 * Run `run` after `ms`, unless `cancel` comes first (S6g, the promote Undo).
 * Pure apart from the timer, so the test drives it with fake timers.
 */
export function deferCommit(run: () => void, ms = PROMOTE_UNDO_MS): DeferredCommit {
  let done = false;
  const fire = () => {
    if (done) return;
    done = true;
    clearTimeout(timer);
    run();
  };
  const timer = setTimeout(fire, ms);
  return {
    cancel() {
      if (done) return false;
      done = true;
      clearTimeout(timer);
      return true;
    },
    flush: fire,
  };
}

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
    description: `It stays in your lists while you are an assignee. Completing it there completes it here.${drops}`,
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
