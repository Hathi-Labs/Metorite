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
 * `promotePlan` turns the answers into ONE `apiMoveTask` request. The store
 * sends it, reads the task back through the lens, and swaps the row in. The
 * card's project label changes in place because it is the same row with a new
 * `project_id` (D53.4). Nothing moves before the server answers.
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
 * gateway's own sentence. It renders IN the card, as the Projects flow does,
 * and goes through the toast seam (`reportSyncFailure`) so a member who
 * closed the card still hears it. A structured 422 also carries the field
 * DEFINITIONS it named, and the card draws those as inputs. Nothing
 * optimistic, nothing to undo.
 *
 * ⚠️ Hosts render this OUTSIDE their clickable root. The Modal portals to
 * `document.body`, but React events bubble the REACT tree, so a dialog
 * rendered inside a card's `onClick` subtree opens the card on every click
 * in it. `TaskCard` and `InboxCard` both host it beside the root, not in it.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { MoveTasksDialog, type PromoteAnswers } from "@/app/projects/components/MoveTasksDialog";
import { projectsApi, refusedFields } from "@/app/projects/lib/api";
import { taskDeepLink } from "@/app/projects/lib/card";
import type { FieldDef } from "@/app/projects/lib/customFields";
import type { ProjectNode } from "@/app/projects/lib/tree";
import { useToast } from "@/components/ui/Toast";

import { promotePlan, promoteToast } from "../lib/promote";
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
}: {
  item: MyTask;
  onClose: () => void;
}) {
  const promoteItem = useTaskStore((s) => s.promoteItem);
  const reportSyncFailure = useTaskStore((s) => s.reportSyncFailure);
  const toast = useToast();
  const router = useRouter();

  const [roots, setRoots] = useState<ProjectNode[]>([]);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The fields a 422 named, drawn as inputs on the next attempt.
  const [refused, setRefused] = useState<FieldDef[]>([]);
  // Seeded ONCE, when the dialog opens. The owners the member started from
  // are the baseline "unchanged" is judged against; re-deriving them on
  // every render would move the baseline under the member's edits.
  const [initialAssignees] = useState(() => ownerIds(item));

  // The destination picker wants the TREE, folders and all, so the member
  // sees the same shape they know from /projects. The store's flat list is
  // for the card label, not for choosing.
  useEffect(() => {
    let live = true;
    projectsApi
      .tree()
      .then((res) => {
        if (live) setRoots(res.rows as ProjectNode[]);
      })
      .catch((err) => {
        if (live) setTreeError(String((err as Error).message));
      });
    return () => {
      live = false;
    };
  }, []);

  async function confirm(
    destinationId: string,
    dropped: string[] | null,
    answers?: PromoteAnswers,
  ) {
    const plan = promotePlan({
      destinationId,
      fields: answers?.fields ?? [],
      draft: answers?.draft ?? {},
      assignees: answers?.assignees ?? [],
      initialAssignees: answers?.initialAssignees ?? [],
    });
    if (!plan.ok) {
      // The dialog holds the button while a field is blank, so this is the
      // belt to that brace: never send what the server will refuse.
      setError(`Fill in ${plan.missing.join(", ")} first.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const outcome = await promoteItem(item.id, plan.request);
      // Read AFTER the move: `promoteItem` re-reads the company list when
      // the destination is new to it, and the render's `projects` is older.
      const projectId = outcome.left ? outcome.projectId : outcome.item.projectId;
      const name =
        useTaskStore.getState().projects.find((p) => p.id === projectId)?.outcome ??
        "the project";
      onClose();
      toast.show({
        key: `tasks-promote:${item.id}`,
        variant: "success",
        ...promoteToast(outcome, name, dropped),
        action: {
          label: "Open board",
          // The id survives the move (D53.4: same row), so the deep link
          // holds whether or not the task is still in my list.
          onClick: () => router.push(taskDeepLink({ id: item.id })),
        },
      });
    } catch (err) {
      const message =
        err instanceof Error && err.message ? err.message : "Couldn't move the task.";
      setError(message);
      setRefused(refusedFields(err) as FieldDef[]);
      reportSyncFailure(`Couldn't move it: ${message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <MoveTasksDialog
      taskIds={[item.id]}
      roots={roots}
      busy={busy}
      error={error ?? treeError}
      promote={{ initialAssignees, due: item.dueAt ?? null, refusedFields: refused }}
      onClose={onClose}
      onConfirm={(destinationId, _statusMap, dropped, answers) =>
        void confirm(destinationId, dropped, answers)
      }
    />
  );
}
