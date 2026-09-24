"use client";

/**
 * My Tasks · the promote door (my_tasks_cutover.md §5 S6c, H-59 (1)).
 *
 * "Move to project…" on a card or in the detail panel opens THIS: the
 * Projects app's own `MoveTasksDialog`, in its promote mode. §4.8 point 5 is
 * the rule — one dialog, never a second drawing — and the mode adds the two
 * questions a promotion needs on top of "where": the destination's required
 * fields (migration 192) and who owns the task there.
 *
 * ## What happens on Move
 *
 * `promotePlan` turns the answers into ONE `apiMoveTask` request. The card
 * closes, and the store waits `PROMOTE_UNDO_MS` before it sends the request
 * (S6g). The toast reads "Moving to {project}… Undo", and Undo cancels it
 * before anything reaches the server. After the send there is no Undo,
 * because D62 refuses a move back into my tree, and the toast offers "Open
 * board". The store reads the task back through the lens and swaps the row
 * in. The card's origin chip changes in place because it is the same row with
 * a new `project_id` (D53.4). Nothing moves before the server answers.
 *
 * A promote that handed the task to a colleague takes it OUT of my list: the
 * read-back answers 404, the store drops the row, and the toast says where it
 * went and to whom (`PromoteOutcome.left`). The move committed; that is not a
 * failure and is never reported as one.
 *
 * ## What happens on a refusal
 *
 * D62 (into somebody's personal tree), the assign guard (a colleague on a
 * task with no project) and a blank required field all come back as the
 * gateway's own sentence, through the toast seam (`reportSyncFailure`). The
 * card has closed by then (S6g), so the toast is where the member hears it.
 * The card already asked for every required field the preview named, so a
 * refusal for a blank field is rare. Nothing optimistic, nothing to undo.
 *
 * ⚠️ Hosts render this OUTSIDE their clickable root. The Modal portals to
 * `document.body`, but React events bubble the REACT tree, so a dialog
 * rendered inside a card's `onClick` subtree opens the card on every click
 * in it. `TaskCard` and `InboxCard` both host it beside the root, not in it.
 */

import { useState } from "react";

import { MoveTasksDialog, type PromoteAnswers } from "@/app/projects/components/MoveTasksDialog";

import { useCompanyTree } from "../lib/companyTree";
import { promotePlan } from "../lib/promote";
import { useTaskStore } from "../lib/taskStore";
import type { MyTask } from "../lib/types";

/** The owners as the gateway names them — an email, or `agent:<name>`. */
function ownerIds(item: MyTask): string[] {
  const people = item.assignees?.length
    ? item.assignees
    : item.assignee
      ? [item.assignee]
      : [];
  return people
    .map((p) => (p.email ?? p.name).trim())
    .filter(Boolean);
}

export function PromoteDialog({
  item,
  onClose,
  initialDestination,
}: {
  item: MyTask;
  onClose: () => void;
  /** S6g — the capture door opens the card on the `#Project` it named. */
  initialDestination?: string | null;
}) {
  const promoteItem = useTaskStore((s) => s.promoteItem);
  const schedulePromote = useTaskStore((s) => s.schedulePromote);
  const reportSyncFailure = useTaskStore((s) => s.reportSyncFailure);
  const backend = useTaskStore((s) => s.backend);

  // The destination picker wants the TREE, folders and all — the same list
  // Clarify's Where step and the capture chip read (`useCompanyTree`). Read
  // again on open, so a project made since the page loaded is offered.
  const { roots, error: treeError } = useCompanyTree(backend === "live", true);
  const [error, setError] = useState<string | null>(null);
  // Seeded ONCE, when the dialog opens. The owners the member started from
  // are the baseline "unchanged" is judged against.
  const [initialAssignees] = useState(() => ownerIds(item));

  function confirm(destinationId: string, dropped: string[] | null, answers?: PromoteAnswers) {
    // The ONE builder both doors use (`promote.test.ts` holds Clarify to it).
    const plan = promotePlan({
      destinationId,
      fields: answers?.fields ?? [],
      draft: answers?.draft ?? {},
      assignees: answers?.assignees ?? [],
      initialAssignees: answers?.initialAssignees ?? [],
    });
    if (!plan.ok) {
      // The card holds the button while a field is blank, so this is the
      // belt to that brace: never send what the server will refuse.
      setError(`Fill in ${plan.missing.join(", ")} first.`);
      return;
    }
    const name = nodeName(roots, destinationId) ?? "the project";
    onClose();
    // S6g — the deferred commit. Nothing is sent for `PROMOTE_UNDO_MS`, and
    // the toast's Undo cancels it. After the send there is no Undo (D62).
    schedulePromote({
      id: item.id,
      projectName: name,
      dropped,
      commit: () =>
        promoteItem(item.id, plan.request).catch((err: unknown) => {
          const message =
            err instanceof Error && err.message ? err.message : "Couldn't move the task.";
          reportSyncFailure(`Couldn't move it: ${message}`);
          throw err;
        }),
    });
  }

  return (
    <MoveTasksDialog
      taskIds={[item.id]}
      roots={roots}
      error={error ?? treeError}
      initialDestination={initialDestination ?? null}
      promote={{ initialAssignees, due: item.dueAt ?? null }}
      onClose={onClose}
      onConfirm={(destinationId, _statusMap, dropped, answers) =>
        confirm(destinationId, dropped, answers)
      }
    />
  );
}

/** A node's name anywhere in the tree. */
function nodeName(
  roots: readonly { id: string; name: string; children?: unknown[] }[],
  id: string,
): string | null {
  for (const node of roots) {
    if (node.id === id) return node.name;
    const kids = (node.children ?? []) as { id: string; name: string; children?: unknown[] }[];
    const hit = nodeName(kids, id);
    if (hit) return hit;
  }
  return null;
}
