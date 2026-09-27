"use client";

/**
 * My Tasks · the ONE subtask question, hosted once (D-PM-38 decisions 2 and
 * 4, Subtasks S5).
 *
 * The store sets `subtaskPrompt` when a gesture would complete or archive a
 * task that has subtasks: the tick, Mark done, a drag or a status pick into
 * Done, and Archive (one task, or the bulk bar). This draws the question the
 * Projects app draws, from the same shared components:
 *
 * - complete: "3 subtasks are still open. Complete them too?" [Only this
 *   task] is focused, and Escape means it. The parent completes either way.
 * - archive: the archive dialog with "Include N subtasks" TICKED. Cancel
 *   archives nothing.
 *
 * Mounted ONCE, in `AppShell`, beside `FocusSession`. The store is global:
 * Focus Mode and the Calendar complete and archive tasks too, and a prompt
 * with no host would swallow the gesture (review of #493). Outside every
 * row, so a click inside it never reaches a card's own click handler.
 */

import { useState } from "react";

import { CompleteSubtasksDialog, IncludeSubtasksBox } from "@/components/SubtaskCascade";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { CASCADE_DEFAULTS } from "@/lib/subtaskCascade";

import { useTaskStore } from "../lib/taskStore";

export function SubtaskPromptHost() {
  const prompt = useTaskStore((s) => s.subtaskPrompt);
  const answer = useTaskStore((s) => s.answerSubtaskPrompt);
  const cancel = useTaskStore((s) => s.cancelSubtaskPrompt);
  const archiving = prompt?.kind === "archive";
  // Keyed on the question, so each archive dialog opens with the box ticked.
  const key = prompt ? `${prompt.kind}:${prompt.ids.join(",")}` : "none";
  return (
    <>
      <CompleteSubtasksDialog
        open={prompt?.kind === "complete" ? prompt.count : 0}
        subject={prompt?.title}
        onAnswer={answer}
      />
      <ArchiveWithSubtasks
        key={key}
        open={archiving}
        count={archiving ? (prompt?.count ?? 0) : 0}
        tasks={prompt?.ids.length ?? 0}
        title={prompt?.title}
        onConfirm={answer}
        onCancel={cancel}
      />
    </>
  );
}

function ArchiveWithSubtasks({
  open,
  count,
  tasks,
  title,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  count: number;
  tasks: number;
  title?: string;
  onConfirm: (includeSubtasks: boolean) => void;
  onCancel: () => void;
}) {
  const [withSubtasks, setWithSubtasks] = useState<boolean>(CASCADE_DEFAULTS.archive);
  return (
    <ConfirmDialog
      open={open}
      title={tasks > 1 ? `Archive ${tasks} tasks?` : "Archive this task?"}
      subject={title}
      body="It leaves every board, list and search. You can restore it from the archive."
      confirmLabel="Archive"
      confirmVariant="primary"
      icon="Archive"
      onCancel={onCancel}
      onConfirm={() => onConfirm(withSubtasks)}
    >
      <IncludeSubtasksBox count={count} checked={withSubtasks} onChange={setWithSubtasks} />
    </ConfirmDialog>
  );
}
