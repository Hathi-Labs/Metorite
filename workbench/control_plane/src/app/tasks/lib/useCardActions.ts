"use client";

import type { PanelAnchor } from "@/components/ui/AnchoredPanel";
import { CATEGORY_LABEL } from "@/lib/statusCategory";
import type { LensLane } from "./lens";
import { useTaskStore } from "./taskStore";
import { MyTask } from "./types";
import { promoteAllowed } from "./promote";
import {
  NEXT_CATEGORIES,
  type NextCategory,
  nextCategoryOf,
} from "./statusCategory";

// One place that turns a Next-Action card's affordances (schedule / change
// status / mark done / eliminate) into store writes — shared by the card's
// inline controls (status pill, Schedule button) and its right-click context
// menu, so the two never drift.
//
// D79, "stages group, statuses write". The status choices are the REAL
// statuses of the task's own status set, listed by `StatusMenu` and written
// by id (`setStatus`). The pill still shows the task's own lane NAME
// (`workflowStage`), so "Building" in one project and "In progress" in
// another both sit under In progress. A stage gesture with no status in
// it (the demo's ChipMenu) goes through `setStage`, which asks when the
// stage holds two or more statuses.
export function useCardActions(item: MyTask) {
  const openSchedule = useTaskStore((s) => s.openSchedule);
  const openEliminate = useTaskStore((s) => s.openEliminate);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const setStage = useTaskStore((s) => s.setStage);
  const setStatus = useTaskStore((s) => s.setStatus);

  const currentCategory = nextCategoryOf(item);
  const isDone = item.disposition === "DONE";

  return {
    schedule: () => openSchedule(item.id),
    eliminate: () => openEliminate(item.id),
    toggleDone: () => quickDispose(item.id, isDone ? "NEXT" : "DONE"),
    /** Put the task in one exact status of its set. */
    setStatus: (statusId: string, lanes?: readonly LensLane[]) =>
      void setStatus(item.id, statusId, lanes ? { lanes } : undefined),
    /** Move the task into a stage. It asks when the stage holds two or more. */
    setStage: (c: NextCategory, anchor?: () => PanelAnchor | null) =>
      void setStage(item.id, c, anchor ? { anchor } : undefined),
    /** The status the menu checks: the id, or the stage on the demo backend. */
    currentStatusId: item.statusId ?? currentCategory ?? undefined,
    categories: NEXT_CATEGORIES,
    categoryLabel: (c: NextCategory) => CATEGORY_LABEL[c],
    currentCategory,
    /** The task's own lane name, for the pill. Falls back to the category. */
    laneName:
      item.workflowStage ??
      (currentCategory ? CATEGORY_LABEL[currentCategory] : "—"),
    isDone,
    /** S6c — whether "Move to project…" is offered. `promoteAllowed` is the rule. */
    canPromote: promoteAllowed(item),
  };
}
