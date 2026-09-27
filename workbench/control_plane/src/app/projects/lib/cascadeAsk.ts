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

import { openSubtasks, type SubtaskCounts } from "@/lib/subtaskCascade";

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
