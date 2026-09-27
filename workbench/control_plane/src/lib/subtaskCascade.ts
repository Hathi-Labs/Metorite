/**
 * What a lifecycle act on a parent does to its subtasks — the client half of
 * D-PM-38 decisions 2 and 4 (Subtasks S5, spec §12.9 and §11.41).
 *
 * ONE module, read by Projects and My Tasks, so both apps ask the same
 * question in the same words and put a cascade back the same way.
 *
 * - **Decision 2 — complete ASKS, and never blocks.** A parent with open
 *   subtasks opens a prompt: "3 subtasks are still open. Complete them too?"
 *   with [Only this task] (the default, focused, and what Escape means) and
 *   [Complete all]. A closed parent with open children stays legal.
 * - **Decision 4 — move and archive take the subtree along.** Each dialog
 *   shows "Include N subtasks", TICKED, and only when N > 0.
 *
 * The gateway defaults `include_subtasks` to FALSE on every door, so an old
 * caller and the chat tools keep acting on one task. These defaults are the
 * UI's, and only the UI sends `true`.
 *
 * Pure: no React, no fetch. `revertCascade` takes its two effects as
 * arguments, so its rules are tests rather than clicks.
 */

/** A task's subtask chip, as the list and the lens send it. */
export interface SubtaskCounts {
  done: number;
  total: number;
}

/**
 * The open subtasks the chip counts: visible to the reader and not archived.
 * These are the DIRECT children. The gateway's cascade closes every open
 * descendant, so the receipt reports the server's count, not this one.
 */
export function openSubtasks(counts: SubtaskCounts | null | undefined): number {
  if (!counts) return 0;
  return Math.max(0, (counts.total ?? 0) - (counts.done ?? 0));
}

/** The box and the prompt both start from these (owner, 2026-09-26). */
export const CASCADE_DEFAULTS = {
  /** Decision 2: the prompt's default is "Only this task". */
  complete: false,
  /** Decision 4: the box is ticked. */
  move: true,
  archive: true,
} as const;

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** "1 subtask" / "3 subtasks". */
export function subtaskCount(n: number): string {
  return `${n} ${plural(n, "subtask", "subtasks")}`;
}

/**
 * The complete prompt's words, or `null` when there is nothing to ask.
 *
 * `cancel` is "Only this task". It is focused, Escape means it, and it still
 * completes the parent: the member asked to complete it, and the prompt only
 * asks about the children.
 */
export function completePrompt(open: number): {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
} | null {
  if (open <= 0) return null;
  const are = plural(open, "is", "are");
  const them = plural(open, "it", "them");
  return {
    // The title and the body read as the owner's one sentence: "3 subtasks
    // are still open. Complete them too?"
    title: `${subtaskCount(open)} ${are} still open`,
    body: `Complete ${them} too? "Only this task" completes this task and leaves ${them} open.`,
    confirmLabel: "Complete all",
    cancelLabel: "Only this task",
  };
}

/** The pre-ticked box in the move and archive dialogs. */
export function includeSubtasksLabel(n: number): string {
  return `Include ${subtaskCount(n)}`;
}

/** "Completed" / "Completed · and 3 subtasks". */
export function completeReceipt(cascaded: number): string {
  return cascaded > 0 ? `Completed · and ${subtaskCount(cascaded)}` : "Completed";
}

/** "Task archived" / "Archived · and 3 subtasks". */
export function archiveReceipt(cascaded: number): string {
  return cascaded > 0 ? `Archived · and ${subtaskCount(cascaded)}` : "Task archived";
}

/** "Moved 4 tasks to Website relaunch", counting the subtasks it took. */
export function moveReceipt(moved: number, cascaded: number, destination?: string): string {
  const total = moved + cascaded;
  const where = destination ? ` to ${destination}` : "";
  return `Moved ${total} ${plural(total, "task", "tasks")}${where}`;
}

/** The bulk bar's line about the parents in a selection. */
export interface BulkSubtaskSummary {
  /** Selected tasks that have at least one open subtask. */
  parents: number;
  /** Their open subtasks, summed over the chip counts. */
  open: number;
}

/**
 * How many of the selected tasks are parents with open subtasks.
 *
 * ⚠️ A subtask that is ITSELF selected is still counted under its parent's
 * chip. The chip is the only count the client has, and the bulk bar says
 * "includes", not "adds". The server skips a task it already closed.
 */
export function bulkSubtaskSummary(
  rows: readonly { id: string; subtasks?: SubtaskCounts | null }[],
  selected: ReadonlySet<string>,
): BulkSubtaskSummary {
  let parents = 0;
  let open = 0;
  for (const row of rows) {
    if (!selected.has(row.id)) continue;
    const n = openSubtasks(row.subtasks);
    if (n > 0) {
      parents += 1;
      open += n;
    }
  }
  return { parents, open };
}

/** "includes 2 parents with 5 open subtasks", or null when there are none. */
export function bulkSummaryLine(summary: BulkSubtaskSummary): string | null {
  if (summary.parents <= 0) return null;
  return (
    `includes ${summary.parents} ${plural(summary.parents, "parent", "parents")} ` +
    `with ${summary.open} open ${plural(summary.open, "subtask", "subtasks")}`
  );
}

/** One status the cascade wrote: what Undo puts back (gateway `Cascade`). */
export interface CascadeChange {
  task_id: string;
  from_status_id: string;
  to_status_id: string;
}

/** What Undo did for each task. */
export interface RevertOutcome {
  restored: string[];
  /** Somebody moved the task since, so its status was kept. */
  movedSince: string[];
  failed: { id: string; reason: string }[];
}

/**
 * Put back the EXACT prior status of each task the cascade moved (D79).
 *
 * Per task, and each the same way My Tasks' single Undo does it
 * (`taskStore.revertStatus`): read the task, and write the prior status only
 * while the task still holds the status the cascade set. The write carries
 * the read's `updated_at` as If-Match, so a move that lands between the read
 * and the write answers 412 and is KEPT, never overwritten.
 *
 * Sequential on purpose: the parent and its children share one board, and
 * the effects re-read what they write.
 */
export async function revertCascade(
  changes: readonly CascadeChange[],
  io: {
    read: (id: string) => Promise<{ status_id: string; updated_at?: string | null }>;
    write: (id: string, statusId: string, ifMatch: string | null) => Promise<unknown>;
  },
): Promise<RevertOutcome> {
  const out: RevertOutcome = { restored: [], movedSince: [], failed: [] };
  for (const change of changes) {
    const id = change.task_id;
    try {
      const now = await io.read(id);
      if (now.status_id !== change.to_status_id) {
        out.movedSince.push(id);
        continue;
      }
      await io.write(id, change.from_status_id, now.updated_at ?? null);
      out.restored.push(id);
    } catch (err) {
      const status = (err as { status?: number } | null)?.status;
      if (status === 412) out.movedSince.push(id);
      else out.failed.push({ id, reason: err instanceof Error ? err.message : String(err) });
    }
  }
  return out;
}

/** What the Undo toast says when some tasks could not be put back. */
export function revertSummary(outcome: RevertOutcome): string | null {
  const kept = outcome.movedSince.length;
  const failed = outcome.failed.length;
  if (!kept && !failed) return null;
  const parts: string[] = [];
  if (kept) {
    parts.push(
      `${kept} ${plural(kept, "task was", "tasks were")} changed by someone since, so ${plural(kept, "it was", "they were")} not undone.`,
    );
  }
  if (failed) {
    parts.push(`${failed} could not be undone: ${outcome.failed[0].reason}`);
  }
  return parts.join(" ");
}
