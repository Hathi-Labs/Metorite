// Start focus on a task from anywhere (CC-4, My Day command centre).
//
// One session at a time. The start reads the task by id when the store never
// hydrated, ends the session that runs, and moves a company task out of its
// to-do stage onto In progress (MD-Q6) with an Undo. This file holds the
// rules as pure functions, and `useStartFocus` is the one door every surface
// calls: the Calendar, the Projects panel and menu, and My Day.

import { useCallback } from "react";

import { ProjectsApiError, projectsApi } from "@/app/projects/lib/api";
import { landingLane, type Placeable } from "@/app/projects/lib/statusOrder";
import { useToast } from "@/components/ui/Toast";

import { isPersonalTask } from "./clarify";
import { lensGetItem, lensSetStatusId } from "./lens";
import { useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

/** The stages that count as "not started" for the board move. */
const NOT_STARTED = new Set(["backlog", "todo"]);

/**
 * The lane a company task moves to when focus starts, or null for no move.
 * Only a task in backlog or todo moves, and only on a company board. A
 * personal task, and a task already in progress or later, stay as they are.
 * The lane is the first `in_progress` lane in stage order, the same rule as a
 * drop on the In progress column (`landingLane`).
 */
export function focusMoveLane(
  item: Pick<MyTask, "projectId" | "rootProjectId" | "statusId" | "statusCategory">,
  personalRootId: string | null,
  areaIds: readonly string[],
  lanes: readonly Placeable[],
): Placeable | null {
  if (!item.projectId || !item.statusCategory) return null;
  if (!NOT_STARTED.has(item.statusCategory)) return null;
  if (isPersonalTask(item, personalRootId, areaIds)) return null;
  if (personalRootId !== null && item.rootProjectId === personalRootId) return null;
  const lane = landingLane(lanes, "in_progress");
  return lane && lane.id !== item.statusId ? lane : null;
}

/** The lanes of the task's project, or none when the read fails. */
async function lanesOf(projectId: string): Promise<Placeable[]> {
  try {
    const res = await projectsApi.statuses(projectId);
    return res.rows.map((r) => ({
      id: r.id,
      name: r.name,
      category: r.category,
      position: r.position,
    }));
  } catch {
    return [];
  }
}

type Toast = ReturnType<typeof useToast>;

/**
 * Take the focus move back, unless the task moved on since. The row is read
 * first: a status other than the lane we wrote means a teammate moved it, so
 * we leave it. The write carries `If-Match`, so a move that lands between the
 * read and the write answers 412 and is kept too (D-PM-20).
 */
export async function undoFocusMove(
  taskId: string,
  movedToId: string,
  before: Pick<MyTask, "statusId" | "workflowStage" | "statusCategory">,
  patchLocal: (p: Partial<MyTask>) => void,
): Promise<"undone" | "changed" | "failed"> {
  if (!before.statusId) return "failed";
  try {
    const now = await lensGetItem(taskId);
    if (now.statusId !== movedToId) return "changed";
    await lensSetStatusId(taskId, before.statusId, { ifMatch: now.updatedAt || undefined });
  } catch (e) {
    return e instanceof ProjectsApiError && (e.status === 412 || e.status === 409) ? "changed" : "failed";
  }
  patchLocal(before);
  return "undone";
}

/**
 * Move a company task out of its to-do stage onto In progress (MD-Q6), then
 * say so with an Undo. It does nothing for any other task, and a failed read
 * or write leaves the task where it is. Focus starts either way.
 */
async function moveToInProgress(item: MyTask, toast: Toast): Promise<void> {
  const taskId = item.id;
  if (!item.projectId || !item.statusCategory || !NOT_STARTED.has(item.statusCategory)) return;
  const { personalRootId, areas } = useTaskStore.getState();
  const lane = focusMoveLane(
    item,
    personalRootId,
    areas.map((a) => a.id),
    await lanesOf(item.projectId),
  );
  if (!lane) return;
  const before = {
    statusId: item.statusId,
    workflowStage: item.workflowStage,
    statusCategory: item.statusCategory,
  };
  const patchLocal = (p: Partial<MyTask>) =>
    useTaskStore.setState((s) => ({
      items: s.items.map((i) => (i.id === taskId ? { ...i, ...p } : i)),
    }));
  try {
    await lensSetStatusId(taskId, lane.id);
  } catch {
    return;
  }
  patchLocal({ statusId: lane.id, workflowStage: lane.name, statusCategory: "in_progress" });
  toast.show({
    key: `focus:moved:${taskId}`,
    title: "Moved to In progress",
    description: `${item.title} is on the board as ${lane.name}.`,
    action: {
      label: "Undo",
      onClick: () => {
        if (!before.statusId) return;
        void undoFocusMove(taskId, lane.id, before, patchLocal).then((outcome) => {
          if (outcome === "changed") {
            toast.show({
              variant: "error",
              title: "The task changed since",
              description: "Someone moved it after you started focus. Set the status on the board.",
            });
          } else if (outcome === "failed") {
            toast.show({
              variant: "error",
              title: "Could not undo the move",
              description: "Set the status on the board.",
            });
          }
        });
      },
    },
  });
}

/**
 * Returns `start(taskId, opts)`. `minimized` starts the session as the dock,
 * for a start where the Now card shows the timer (My Day).
 */
/** Task ids whose start is still running. A double click starts once. */
const starting = new Set<string>();

export function useStartFocus(): (
  taskId: string,
  opts?: { minimized?: boolean },
) => Promise<void> {
  const toast = useToast();
  return useCallback(
    async (taskId, opts) => {
      if (starting.has(taskId)) return;
      starting.add(taskId);
      try {
        await startOne(taskId, opts, toast);
      } finally {
        starting.delete(taskId);
      }
    },
    [toast],
  );
}

async function startOne(
  taskId: string,
  opts: { minimized?: boolean } | undefined,
  toast: Toast,
): Promise<void> {
  // The same task again only changes how the room shows.
  if (useTaskStore.getState().focusSessionId === taskId) {
    useTaskStore.setState({ focusMinimized: opts?.minimized ?? false });
    return;
  }
  await useTaskStore.getState().ensureLive();
  let item = useTaskStore.getState().items.find((i) => i.id === taskId);
  if (!item) {
    try {
      item = await lensGetItem(taskId);
      useTaskStore.getState().adoptItem(item);
    } catch {
      toast.show({
        key: "focus:start",
        variant: "error",
        title: "Could not start focus",
        description: "The task did not load. Try again in a moment.",
      });
      return;
    }
  }
  // The board move runs FIRST. The room's own writes (the actual start)
  // answer with the task row, and a row read before the move commits
  // would put the old stage back into the store.
  await moveToInProgress(item, toast);

  // One session at a time: a second start ends the first and stamps its
  // actual end, as the room's own end does.
  const prev = useTaskStore.getState().focusSessionId;
  if (prev && prev !== taskId) {
    const cur = useTaskStore.getState().items.find((i) => i.id === prev);
    if (cur?.actualStart && !cur.actualEnd) {
      useTaskStore.getState().updateItem(prev, { actualEnd: new Date().toISOString() });
    }
  }
  useTaskStore.getState().enterFocusSession(taskId, opts);
}
