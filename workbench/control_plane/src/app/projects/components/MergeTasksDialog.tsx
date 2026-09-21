"use client";

/**
 * Projects · "Merge into…" — the card that asks which task survives.
 *
 * Owner request, 2026-09-21: *"if I right click on a task, I should be able
 * to merge it into another task."*
 *
 * ## Why this is a card and not a click
 *
 * `MoveTasksDialog` opens because a move crosses two vocabularies. This opens
 * for a harder reason: **a merge moves comments, history and attachments one
 * way, and running it again does not undo it.** The single decision inside it
 * — which task is kept — is the one a member must not make by accident, so it
 * is named in the summary, named again on the button, and the chosen row is
 * the only one that looks chosen.
 *
 * ## The order on screen is the argument
 *
 * What is being merged, then the search, then the list, then what will happen
 * in words, then the button. The consequence sits directly above the button
 * and never in a toast afterwards — `MoveTasksDialog`'s rule (D-PM-29), and a
 * merge is the less reversible of the two.
 *
 * ## One thing this card deliberately does NOT say
 *
 * It does not preview the combined values. Summing two estimates and showing
 * the result would be a second implementation of `merge.py::_fold_scalars`,
 * in a language that cannot see the database, and the two would drift — the
 * mirror problem that has already cost this repo a 422 today. The card states
 * the SHAPE of what happens, which is stable, and the panel shows the result.
 */

import { useEffect, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import type { TaskRow } from "../lib/api";
import {
  type MergeCandidate,
  matchCandidates,
  mergeCandidates,
  mergeSummary,
} from "../lib/taskMerge";

interface Props {
  /** The tasks to fold in. Empty or null closes the card. */
  sources: readonly string[] | null;
  /** Everything loaded for this project — the candidates to keep. */
  tasks: readonly TaskRow[];
  busy: boolean;
  error: string | null;
  onClose(): void;
  onMerge(targetId: string): void;
}

export function MergeTasksDialog({
  sources,
  tasks,
  busy,
  error,
  onClose,
  onMerge,
}: Props) {
  const open = Boolean(sources && sources.length);
  const [query, setQuery] = useState("");
  const [targetId, setTargetId] = useState<string | null>(null);

  // A fresh card each time. Without this, reopening it offers the target
  // chosen for a different set of tasks, already selected — which is the one
  // mistake this card exists to prevent, arriving pre-made.
  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQuery("");
    setTargetId(null);
  }, [open, sources]);

  const chosen = sources ?? [];
  const candidates = useMemo(
    () => mergeCandidates(tasks, chosen),
    // `chosen` is a fresh array each render when `sources` is null, so the
    // dependency is on `sources` itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tasks, sources],
  );
  const shown = useMemo(
    () => matchCandidates(candidates, query),
    [candidates, query],
  );
  const target = useMemo(
    () => tasks.find((t) => t.id === targetId) ?? null,
    [tasks, targetId],
  );

  if (!open) return null;

  return (
    <Modal open onClose={onClose} title="Merge tasks" icon="Merge">
      <div className="flex flex-col gap-3">
        <p className="text-xs text-muted-foreground">
          {chosen.length === 1
            ? "This task will be folded into the one you choose."
            : `These ${chosen.length} tasks will be folded into the one you choose.`}
        </p>

        <Input
          inputSize="md"
          icon="Search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find the task to keep — number or title…"
          aria-label="Find the task to keep"
        />

        {/* Scrolls rather than growing: a project with four hundred tasks
            would otherwise push the button off the screen, and the button is
            the thing this card exists to put in front of somebody. */}
        <ul className="max-h-64 overflow-y-auto rounded-md border border-border">
          {shown.map(({ task, refusal }: MergeCandidate) => {
            const picked = task.id === targetId;
            return (
              <li key={task.id}>
                <button
                  type="button"
                  disabled={Boolean(refusal) || busy}
                  onClick={() => setTargetId(task.id)}
                  className={`flex w-full items-start gap-2 border-b border-border px-2.5 py-2 text-left text-xs last:border-b-0 ${
                    picked
                      ? "bg-primary/10 text-foreground"
                      : refusal
                        ? "cursor-not-allowed opacity-50"
                        : "hover:bg-secondary/50"
                  }`}
                >
                  <Icon
                    name={picked ? "CircleCheck" : "Circle"}
                    className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${
                      picked ? "text-primary" : "text-muted-foreground"
                    }`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">
                      <span className="text-muted-foreground">
                        #{task.task_number}
                      </span>{" "}
                      {task.title}
                    </span>
                    {refusal ? (
                      <span className="block text-[11px] text-muted-foreground">
                        {refusal}
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            );
          })}
          {shown.length === 0 ? (
            <li className="px-2.5 py-3 text-xs text-muted-foreground">
              No task here matches that.
            </li>
          ) : null}
        </ul>

        {/* Directly above the button, never in a toast afterwards. */}
        <p className="rounded-md border border-border bg-card px-2.5 py-2 text-xs text-muted-foreground">
          {mergeSummary(target, chosen)}
        </p>

        {error ? (
          <p className="text-xs font-medium text-destructive">{error}</p>
        ) : null}

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          {/* The survivor is named ON the button, so the last thing read
              before the irreversible click is which task is kept. */}
          <Button
            icon="Merge"
            loading={busy}
            disabled={!targetId || busy}
            onClick={() => targetId && onMerge(targetId)}
          >
            {target ? `Merge into #${target.task_number}` : "Choose a task"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
