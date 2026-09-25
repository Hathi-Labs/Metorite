"use client";

import Icon from "@/components/Icon";
import { StatusChip } from "@/components/StatusChip";
import { useState } from "react";
import { MyTask } from "../lib/types";
import { useCardActions } from "../lib/useCardActions";
import { categoryAccent, laneAccent } from "../lib/stageColors";

// The task's STATUS INDICATOR — the single status control shared by the card
// (TaskCard) and the desktop list's Status column. The pill shows the task's
// own LANE name and is coloured by the lane's stored colour, then its status
// CATEGORY (D73.9), the order Projects uses. Click opens the three categories — To do, In
// progress, Done. Picking one moves the task to the first lane of that
// category in its own project; Done completes it (see useCardActions).
// stopPropagation so it never opens the card/row.
//
// WS-27ad: the pill BODY is `@/components/StatusChip`, the same one /projects
// draws in its list and table cells — the classes are unchanged, they just live
// somewhere both apps can reach. What stays here is the interaction: /projects'
// status is read-only or a `<select>`, this one opens a stage menu.
export function StatusPill({ item }: { item: MyTask }) {
  const { categories, categoryLabel, currentCategory, laneName, setCategory } =
    useCardActions(item);
  const [open, setOpen] = useState(false);
  // The lane's stored colour first, then its category: Projects' order
  // (`laneAccent`). Only the COLOUR changed here. The menu below is as it was.
  const accent = laneAccent(item, currentCategory);
  return (
    <div className="relative shrink-0" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Change status"
        aria-label={`Status: ${laneName}. Click to change.`}
        className="tech-transition inline-flex hover:brightness-95"
      >
        <StatusChip
          accent={accent}
          label={laneName}
          trailing={<Icon name="ChevronDown" className="h-2.5 w-2.5 opacity-60" />}
        />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-50 mt-1 min-w-[150px] overflow-hidden rounded-lg border border-border bg-popover py-1 shadow-xl">
            {categories.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setCategory(s);
                  setOpen(false);
                }}
                className="tech-transition flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs text-foreground hover:bg-secondary"
              >
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${categoryAccent(s).dot}`}
                />
                <span className="min-w-0 flex-1 truncate">{categoryLabel(s)}</span>
                {s === currentCategory && (
                  <Icon name="Check" className="h-3 w-3 shrink-0 text-primary" />
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
