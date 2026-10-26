/**
 * Projects · what a task move must read again (WS-64 #11).
 *
 * `refreshSurface` reloads the selected project, and in My work nothing is
 * selected. So a moved task stayed in the My work list, and an open task
 * panel kept showing the old project and its statuses.
 */
export interface MoveFollowUp {
  /** My work draws from the task store, so the store reads the list again. */
  reloadMyWork: boolean;
  /** The open panel shows a task that moved, so it reads that task again. */
  reopenTaskId: string | null;
}

export function moveFollowUp(opts: {
  myWork: boolean;
  openTaskId: string | null;
  movedIds: readonly string[];
}): MoveFollowUp {
  const { myWork, openTaskId, movedIds } = opts;
  return {
    reloadMyWork: myWork,
    reopenTaskId: openTaskId && movedIds.includes(openTaskId) ? openTaskId : null,
  };
}
