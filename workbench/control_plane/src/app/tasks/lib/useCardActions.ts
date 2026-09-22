"use client";

import { useTaskStore } from "./taskStore";
import { GtdItem } from "./types";
import { lensEnabled } from "./lens";
import { promoteAllowed } from "./promote";
import { statusColumnForItem } from "./ordering";

// One place that turns a Next-Action card's affordances (schedule / change stage
// / mark done / eliminate) into store writes — shared by the card's inline
// controls (stage selector, Schedule button) and its right-click context menu,
// so the two never drift. Stage semantics mirror the board:
//   • the LAST stage is "Done" → quickDispose(DONE) (keeps the undo snapshot);
//   • picking another stage reopens a done task first, then sets workflowStage
//     (a local override that also wins for synced tasks in statusColumnForItem);
//   • updateItem already auto-completes when workflowStage hits the last stage.
export function useCardActions(item: GtdItem) {
  const openSchedule = useTaskStore((s) => s.openSchedule);
  const openEliminate = useTaskStore((s) => s.openEliminate);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const updateItem = useTaskStore((s) => s.updateItem);
  const stages = useTaskStore((s) => s.settings.workflowStages);
  const statusStageMap = useTaskStore((s) => s.settings.statusStageMap);

  const doneStage = stages[stages.length - 1];
  const firstStage = stages[0];
  const currentStage = statusColumnForItem(
    item,
    stages,
    firstStage,
    statusStageMap,
  );
  const isDone = item.disposition === "DONE";

  const setStage = (stage: string) => {
    if (stage === currentStage) return;
    if (stage === doneStage) {
      quickDispose(item.id, "DONE");
      return;
    }
    if (isDone) quickDispose(item.id, "NEXT"); // reopen before re-staging
    updateItem(item.id, { workflowStage: stage });
  };

  return {
    schedule: () => openSchedule(item.id),
    eliminate: () => openEliminate(item.id),
    toggleDone: () => quickDispose(item.id, isDone ? "NEXT" : "DONE"),
    setStage,
    stages,
    doneStage,
    currentStage,
    isDone,
    /** S6c — whether "Move to project…" is offered. `promoteAllowed` is the rule. */
    canPromote: promoteAllowed(item, lensEnabled()),
  };
}
