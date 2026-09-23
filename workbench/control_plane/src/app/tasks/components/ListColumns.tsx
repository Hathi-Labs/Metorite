"use client";

import Icon from "@/components/Icon";
import { MyTask } from "../lib/types";
import { useTaskStore } from "../lib/taskStore";
import { durationLabel, isOverdue, relativeTime } from "../lib/utils";
import { PriorityBadge } from "./PriorityControls";
import { ACTION_MODE_META, actionMode, type ActionMode } from "../lib/priority";
import { MODE_ICON } from "../lib/priorityIcons";
import { contextAccent } from "../lib/contextColors";
import type { ColumnDef } from "../lib/columns";
import { TaskMeta } from "@/components/TaskMeta";
import { importanceChip } from "@/app/projects/lib/card";
import { taskMeta } from "@/lib/taskCard";

// The desktop columnar cells for the Next-Actions list. Each renders the SAME
// visual that signal has as a card pill — just placed in its own aligned grid
// column instead of wrapping under the title. Mobile never uses these (it keeps
// the stacked TaskCard row); see TaskListGrouped's sm: breakpoint.

// Due/overdue here read the REAL clock (isOverdue and relativeTime both default
// nowMs to Date.now()). They used to be passed a frozen `MOCK_NOW` demo
// constant, which made every due column progressively wrong.

const ENERGY_DOT: Record<string, string> = {
  low: "bg-success",
  medium: "bg-warning",
  high: "bg-destructive",
};

// Action-mode pill tint. An action mode is a STATE, not a category — there are
// four of them and they mean something — so these are semantic tokens, not the
// categorical ramp `contextAccent` draws from.
//
// `delegate` was orange-500 and `schedule` amber-500: two raw palette hues one
// step apart, unthemed, and so close that the colour never carried the
// distinction anyway. `warning` is the token that means "this needs attention
// from someone"; `do` and `schedule` are both "it stays with you", which is why
// they share `primary`. Each pill also carries its own icon and label
// (DESIGN_SYSTEM §7 — never signal state with colour alone), and that is what
// tells them apart, here as before.
//
// Deliberately NOT routed through `@/lib/statusAccent` (AGENTS.md rule 4). That
// module resolves an unknown lane — a stored colour, a category, a user-typed
// stage name, a position — to a hue. An ActionMode is none of those: it is four
// compile-time constants, `resolveHue` has nothing to resolve, and `keywordHue`
// would match "delegate" against no rule and fall through to the POSITIONAL
// list. Rule 4 forbids a second PALETTE; these are the house tokens themselves,
// the same strings `statusAccent`'s `amber` and `blue` accents emit.
const MODE_TONE: Record<ActionMode, string> = {
  do: "border-primary/40 bg-primary/10 text-primary",
  delegate: "border-warning/40 bg-warning/10 text-warning",
  schedule: "border-primary/40 bg-primary/10 text-primary",
  drop: "border-border bg-secondary/60 text-muted-foreground",
};

const ALIGN: Record<ColumnDef["align"], string> = {
  left: "justify-start text-left",
  center: "justify-center text-center",
  right: "justify-end text-right",
};

/** The Suggestion column's pill — and it ACTS, same as the card nudge:
 *  "Do It"/"Schedule?" open the add-to-calendar popup, "Delegate?" the
 *  eligible-people picker, "Eliminate?" the Someday-or-delete popup.
 *  stopPropagation so the row doesn't also open the task. */
function ModePill({
  item,
  urgentWindowHours,
}: {
  item: MyTask;
  urgentWindowHours?: number;
}) {
  const openSchedule = useTaskStore((s) => s.openSchedule);
  const openEliminate = useTaskStore((s) => s.openEliminate);
  const openDelegate = useTaskStore((s) => s.openDelegate);
  const mode = actionMode(item, urgentWindowHours);
  const meta = ACTION_MODE_META[mode];
  const ModeIcon = MODE_ICON[mode];
  const titles: Record<ActionMode, string> = {
    do: "Yours to do — put it on the calendar",
    schedule: "Schedule it on the calendar",
    delegate: "Pick who to hand this to",
    drop: "Let it go — Someday, or delete",
  };
  const act = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (mode === "delegate") openDelegate(item.id);
    else if (mode === "drop") openEliminate(item.id);
    else openSchedule(item.id);
  };
  return (
    <button
      type="button"
      onClick={act}
      title={titles[mode]}
      className={[
        "tech-transition inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium hover:brightness-110",
        MODE_TONE[mode],
      ].join(" ")}
    >
      <ModeIcon className="h-3 w-3 shrink-0" aria-hidden />
      {meta.label}
    </button>
  );
}

/** The header cell (column label) — matches the grid so headers sit above their
 *  column. Name is rendered by the caller as the flexible first track. */
export function ColumnHeader({ col }: { col: ColumnDef }) {
  return (
    <span
      className={[
        "flex items-center truncate text-[10px] font-semibold uppercase tracking-wide text-muted-foreground",
        ALIGN[col.align],
      ].join(" ")}
    >
      {col.label}
    </span>
  );
}

/** One column's value cell for a task. Renders nothing (an empty aligned cell)
 *  when the task has no value for that column, so the grid stays aligned. */
export function ColumnCell({
  col,
  item,
  urgentWindowHours,
}: {
  col: ColumnDef;
  item: MyTask;
  urgentWindowHours?: number;
}) {
  return (
    <div className={["flex min-w-0 items-center", ALIGN[col.align]].join(" ")}>
      <CellBody col={col} item={item} urgentWindowHours={urgentWindowHours} />
    </div>
  );
}

function CellBody({
  col,
  item,
  urgentWindowHours,
}: {
  col: ColumnDef;
  item: MyTask;
  urgentWindowHours?: number;
}) {
  switch (col.key) {
    case "priority": {
      // D76 — the task's SHARED Priority, drawn with the Projects card's own
      // chip so "Urgent" looks the same in both apps. Unset draws nothing.
      const chip = importanceChip({ importance: item.importance ?? null });
      return chip ? <TaskMeta chips={[chip]} /> : null;
    }
    case "focus":
      // The member's Focus matrix cell (important from Priority × urgent
      // from the due date × their own leveraged flag).
      return (
        <PriorityBadge item={item} urgentWindowHours={urgentWindowHours} />
      );
    case "tags": {
      // D76 — the shared tags, as the card's tag pills.
      const chips = taskMeta({ tags: (item.tags ?? []).map((name) => ({ name })) });
      return chips.length ? <TaskMeta chips={chips} className="min-w-0" /> : null;
    }
    case "mode":
      // The suggestion of what to DO with this task — from the shared
      // actionMode() logic (same as the "Action mode" group-by lens). Shown on
      // every task: "Do It" (yours) or a "?" nudge (Delegate/Schedule/Eliminate).
      return <ModePill item={item} urgentWindowHours={urgentWindowHours} />;
    case "context":
      return item.context ? (
        <span
          className={[
            "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px]",
            contextAccent(item.context).chip,
          ].join(" ")}
        >
          {item.context}
        </span>
      ) : null;
    case "energy":
      return item.energy ? (
        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
          <span className={`h-1.5 w-1.5 rounded-full ${ENERGY_DOT[item.energy]}`} />
          {item.energy}
        </span>
      ) : null;
    case "estimate":
      return item.timeEstimateMins ? (
        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
          <Icon name="Zap" className="h-3 w-3" />
          {durationLabel(item.timeEstimateMins)}
        </span>
      ) : null;
    case "due":
      return item.dueAt ? (
        <span
          className={[
            "inline-flex items-center gap-1 text-[10px]",
            isOverdue(item)
              ? "font-medium text-destructive"
              : "text-muted-foreground",
          ].join(" ")}
        >
          {isOverdue(item) ? (
            <Icon name="AlertTriangle" className="h-3 w-3" />
          ) : (
            <Icon name="Clock" className="h-3 w-3" />
          )}
          {relativeTime(item.dueAt)}
        </span>
      ) : null;
    case "attachments":
      return item.attachments?.length ? (
        <span className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground">
          <Icon name="Paperclip" className="h-3 w-3" />
          {item.attachments.length}
        </span>
      ) : null;
    case "subtasks":
      return item.subtaskCount ? (
        <span className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground">
          <Icon name="ListTree" className="h-3 w-3" />
          {item.subtaskCount}
        </span>
      ) : null;
    default:
      return null;
  }
}
