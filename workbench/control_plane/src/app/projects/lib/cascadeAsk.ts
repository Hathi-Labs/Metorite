/**
 * Projects · when a status change must ask about open subtasks (D-PM-38
 * decision 2, Subtasks S5).
 *
 * Every Projects door that can complete a task reads this one rule: the
 * board's tick and its status menu, a drag into a Done column, the panel's
 * status menu and the table's status cell. Pure, so the rule is a test.
 *
 * It asks only for a move INTO a `done` lane, from a lane that is not
 * closed, of a task with open visible subtasks. A move into `cancelled`
 * never asks: the gateway's cascade closes children only into Done, and
 * nobody asked to cancel them.
 */

import {
  type CascadeChange,
  openSubtasks,
  type SubtaskCounts,
} from "@/lib/subtaskCascade";

import { isResolved } from "./relations";

export interface CascadeLane {
  id: string;
  category: string;
}

/**
 * How many open subtasks to ask about, or 0 for "do not ask".
 *
 * `open` overrides the chip when the caller has a better count: the task
 * panel reads a single task, which carries no chip, so it reads the
 * relations progress instead. That count is the same one (S1 live check b).
 */
export function completionAsk(
  task: { status_id: string; subtasks?: SubtaskCounts | null },
  targetStatusId: string,
  lanes: readonly CascadeLane[],
  open?: number,
): number {
  if (targetStatusId === task.status_id) return 0;
  const target = lanes.find((lane) => lane.id === targetStatusId);
  if (target?.category !== "done") return 0;
  const current = lanes.find((lane) => lane.id === task.status_id);
  if (isResolved(current?.category)) return 0;
  return open ?? openSubtasks(task.subtasks);
}

/**
 * Write the completion, and answer the Undo record for it (D79).
 *
 * ⚠️ The parent's prior status comes from a SERVER read taken just before
 * the write, never from the row on screen. The board's row is as old as its
 * last load: a teammate may have moved the task since, and an Undo that put
 * back the board's stale status would undo their move (review of #493). My
 * Tasks reads the server first for the same reason (`taskStore.quickDispose`).
 *
 * A task the server already holds in `statusId` gets no parent entry: our
 * write moved nothing, so Undo has nothing of ours to put back.
 */
export async function writeCompletion<T extends { subtask_changes?: CascadeChange[] }>(
  io: {
    read: (id: string) => Promise<{ status_id: string }>;
    write: (id: string, statusId: string, includeSubtasks: boolean) => Promise<T>;
  },
  taskId: string,
  statusId: string,
  includeSubtasks: boolean,
): Promise<{ fresh: T; changes: CascadeChange[] }> {
  const before = await io.read(taskId);
  const fresh = await io.write(taskId, statusId, includeSubtasks);
  const parent: CascadeChange[] =
    before.status_id === statusId
      ? []
      : [{ task_id: taskId, from_status_id: before.status_id, to_status_id: statusId }];
  return { fresh, changes: [...parent, ...(fresh.subtask_changes ?? [])] };
}
