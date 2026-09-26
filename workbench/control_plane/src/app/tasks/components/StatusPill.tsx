"use client";

import Icon from "@/components/Icon";
import { StatusChip } from "@/components/StatusChip";
import { StatusMenu } from "@/components/ui/StatusMenu";
import { useState } from "react";
import { MyTask } from "../lib/types";
import { useCardActions } from "../lib/useCardActions";
import { useTaskLanes } from "../lib/useTaskLanes";
import { laneAccent } from "../lib/stageColors";

// The task's STATUS INDICATOR — the single status control shared by the card
// (TaskCard) and the desktop list's Status column. The pill shows the task's
// own LANE name and is coloured by the lane's stored colour, then its status
// CATEGORY (D73.9), the order Projects uses.
//
// D79: a click opens `StatusMenu` with the REAL statuses of the task's own
// status set, grouped by stage. A pick writes that exact status id, and the
// undo toast names it. A pick inside the stage the task is already in works
// too ("Doing" to "In review"), which the old three-category menu could not
// express. stopPropagation so it never opens the card/row.
//
// WS-27ad: the pill BODY is `@/components/StatusChip`, the same one /projects
// draws in its list and table cells.
export function StatusPill({ item }: { item: MyTask }) {
  const { currentCategory, currentStatusId, laneName, setStatus } = useCardActions(item);
  const [open, setOpen] = useState(false);
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);
  const lanes = useTaskLanes(item.id, open);
  // The lane's stored colour first, then its category: Projects' order
  // (`laneAccent`).
  const accent = laneAccent(item, currentCategory);
  return (
    <div className="relative shrink-0" onClick={(e) => e.stopPropagation()}>
      <button
        ref={setTrigger}
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Change status"
        aria-label={`Status: ${laneName}. Click to change.`}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="tech-transition inline-flex hover:brightness-95"
      >
        <StatusChip
          accent={accent}
          label={laneName}
          trailing={<Icon name="ChevronDown" className="h-2.5 w-2.5 opacity-60" />}
        />
      </button>
      <StatusMenu
        anchor={trigger}
        open={open && lanes !== null}
        projectName={item.projectName}
        statuses={lanes ?? []}
        currentId={currentStatusId}
        onPick={(statusId) => {
          setOpen(false);
          trigger?.focus();
          setStatus(statusId, lanes ?? undefined);
        }}
        onClose={() => setOpen(false)}
      />
    </div>
  );
}
