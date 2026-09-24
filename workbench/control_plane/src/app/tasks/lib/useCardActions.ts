"use client";

import { useTaskStore } from "./taskStore";
import { MyTask } from "./types";
import { promoteAllowed } from "./promote";
import {
  CATEGORY_LABEL,
  NEXT_CATEGORIES,
  type NextCategory,
  nextCategoryOf,
} from "./statusCategory";

// One place that turns a Next-Action card's affordances (schedule / change
// status / mark done / eliminate) into store writes — shared by the card's
// inline controls (status pill, Schedule button) and its right-click context
// menu, so the two never drift.
//
// D73.9: the status choices are the three Projects status CATEGORIES, not a
// configured stage list. Picking one resolves to the first lane of that
// category in the task's own project (`setCategory`); Done completes the task.
// The pill still shows the task's own lane NAME (`workflowStage`), so
// "Building" in one project and "In progress" in another both sit under
// In progress.
export function useCardActions(item: MyTask) {
  const openSchedule = useTaskStore((s) => s.openSchedule);
  const openEliminate = useTaskStore((s) => s.openEliminate);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const setCategory = useTaskStore((s) => s.setCategory);

  const currentCategory = nextCategoryOf(item);
  const isDone = item.disposition === "DONE";

  return {
    schedule: () => openSchedule(item.id),
    eliminate: () => openEliminate(item.id),
    toggleDone: () => quickDispose(item.id, isDone ? "NEXT" : "DONE"),
    /** Move the task into a category group. */
    setCategory: (c: NextCategory) => void setCategory(item.id, c),
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
