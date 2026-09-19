"use client";

/**
 * Projects · "Delete project" — WS-27bg slice 2 remainder (H-8).
 *
 * The one irreversible act in the Projects tree, and the only guard in front of
 * it. `DELETE /projects/nodes/{id}` cascades over the subtree, every task and
 * every grant; the server asks nothing. So this dialog does the asking.
 *
 * ## Three things it does, in this order
 *
 * 1. **Reads the real counts first.** `GET /nodes/{id}/summary` — the same
 *    roll-up the dashboards use, not a second aggregate and not a count taken
 *    over rows on screen. Until it answers, the confirm button stays disabled:
 *    a confirmation that lets you agree before it knows what you are agreeing
 *    to is a confirmation in appearance only.
 * 2. **Says what goes, and admits what it did not count.** `NodeSummary`
 *    counts descendant projects and tasks; the cascade also takes folders and
 *    grants, which it does not count. `projectDelete.ts` carries that argument.
 * 3. **Takes the project's name back.** Typed, trimmed, case-insensitive.
 *
 * ⚠️ **If the count read FAILS, the delete is refused, not offered anyway.**
 * The tempting fallback is to show the dialog without numbers and let the
 * member decide. But the numbers are the decision — "delete Roadmap" and
 * "delete Roadmap and the 380 tasks under it" are different questions, and the
 * second one is the one being asked. A dialog that cannot tell them apart must
 * not accept an answer to either.
 */

import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import { type NodeSummary, type ProjectRow, projectsApi } from "../lib/api";
import { confirmsDeletion, deletionClauses, joinClauses } from "../lib/projectDelete";

interface Props {
  /** The row to delete. `null` closes the dialog. */
  project: ProjectRow | null;
  onClose: () => void;
  /** Confirmed. The page owns the call, the toast and the refetch. */
  onConfirm: (project: ProjectRow) => void;
  /** The page is mid-delete. */
  busy?: boolean;
}

export function DeleteProjectDialog({ project, onClose, onConfirm, busy }: Props) {
  const [typed, setTyped] = useState("");
  const [summary, setSummary] = useState<NodeSummary | null>(null);
  const [failed, setFailed] = useState(false);

  const projectId = project?.id ?? null;

  useEffect(() => {
    if (!projectId) return;
    // Reopening on a different row must not show the previous row's counts for
    // the frame before the fetch lands — that frame is exactly where somebody
    // reads "2 tasks", agrees, and destroys four hundred.
    setTyped("");
    setSummary(null);
    setFailed(false);

    let live = true;
    projectsApi
      .summary(projectId)
      .then((res) => {
        if (live) setSummary(res);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [projectId]);

  if (!project) return null;

  const clauses = deletionClauses(summary);
  const counted = joinClauses(clauses);
  const ready = summary !== null;
  const confirmed = confirmsDeletion(typed, project.name);

  return (
    <Modal
      open
      onClose={onClose}
      title="Delete project"
      icon="Trash2"
      size="sm"
      description={`This cannot be undone. ${project.name} and everything under it is removed for everybody in the organisation.`}
    >
      {/* `p-3` matches the header's `px-3`. `Modal` pads its header and
          renders children raw, so a caller that forgets this draws its body
          flush against the dialog edge — which is what the first visual
          capture of this dialog showed. */}
      <div className="space-y-3 p-3 text-xs">
        {failed ? (
          <p className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive">
            Couldn&rsquo;t read what is inside this project, so there is no safe
            way to confirm the delete. Close this and try again.
          </p>
        ) : !ready ? (
          <p className="text-muted-foreground">
            Counting what is inside {project.name}…
          </p>
        ) : (
          <p className="text-foreground">
            {counted
              ? /* "and everything under it" is not padding. The counts exclude
                   folders and grants, which the cascade still takes, and a
                   sentence that stopped at the numbers would read as the
                   complete inventory. */
                `Deleting ${project.name} also deletes ${counted}, and everything else under it.`
              : `${project.name} holds no subprojects and no tasks.`}
          </p>
        )}

        <label className="block space-y-1">
          <span className="text-muted-foreground">
            Type <span className="font-medium text-foreground">{project.name}</span>{" "}
            to confirm.
          </span>
          <Input
            inputSize="sm"
            value={typed}
            autoComplete="off"
            disabled={!ready || busy}
            onChange={(e) => setTyped(e.target.value)}
            aria-label={`Type ${project.name} to confirm deletion`}
          />
        </label>
      </div>

      <div className="flex justify-end gap-2 px-3 pb-3">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          size="sm"
          icon="Trash2"
          loading={busy}
          /* Both gates, and the count one is not redundant: without it a fast
             typist can confirm a name before the dialog knows what the name
             holds. */
          disabled={!ready || !confirmed}
          onClick={() => onConfirm(project)}
        >
          Delete project
        </Button>
      </div>
    </Modal>
  );
}

export default DeleteProjectDialog;
