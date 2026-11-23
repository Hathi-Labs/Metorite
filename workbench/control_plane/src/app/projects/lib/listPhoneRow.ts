/**
 * Projects · what one phone row of the List shows (MOB-2, task 1537).
 *
 * `one_projects_app.md` §9b.4: at 390 px and 360 px a row shows five parts
 * (tick, title, project, due, status) with no sideways scroll, and one tap
 * finishes a task. The vitest runner has no DOM, so every decision the row makes
 * lives here as a pure function and `TaskList.tsx` only draws it.
 *
 * The tick is the board card's tick. It reads the same `taskMenu` rules
 * (`isDone`, `doneTarget`), so the List and the board cannot disagree about
 * which lane "done" is, or whether a task has one.
 */

import { doneLabel } from "@/lib/taskMenuVocabulary";
import type { MetaChip } from "@/lib/taskCard";

import type { StatusRow, TaskRow } from "./api";
import { visibleChips } from "./card";
import { projectChipText } from "./myTasks";
import { doneTarget, isDone } from "./taskMenu";

/** The smallest tick target on a phone, in px. WCAG 2.5.5 asks for 44, the board uses 40. */
export const PHONE_TICK_PX = 40;

/** What the leading control of the row is. */
export type PhoneTick =
  /** A bulk selection is open: a checkbox, as the board's menu would give. */
  | { kind: "select"; label: string; checked: boolean }
  /** The finish tick. `toStatusId` is the lane one tap moves the task to. */
  | { kind: "complete"; label: string; done: boolean; toStatusId: string }
  /** No closed lane, or the surface cannot write: the row has no tick. */
  | { kind: "none" };

export interface PhoneRowInput {
  task: TaskRow;
  /** The statuses of THIS task's project (`statusesFor`, else the list's own). */
  statuses: readonly StatusRow[];
  shownFields: readonly string[];
  /** The path of the project, or null when the surface draws none. */
  path?: readonly string[] | null;
  /** A bulk selection is open (at least one id picked) and the surface can select. */
  selectMode: boolean;
  selected: boolean;
  /** The surface can write a status (it passed `onDrop`). */
  canComplete: boolean;
  nowMs?: number;
}

export interface PhoneRowParts {
  tick: PhoneTick;
  title: string;
  /** The project path, cut the way every other surface cuts it. */
  project: { label: string; title: string } | null;
  /** The due chip, with its overdue tone, or null when the task has no due date. */
  due: MetaChip | null;
  status: StatusRow | null;
}

/** The tick's accessible name: "Mark <title> done" or "Reopen <title>". */
export function tickLabel(title: string, done: boolean): string {
  return doneLabel(done) === "Reopen" ? `Reopen ${title}` : `Mark ${title} done`;
}

/** Decide the row. Pure: the same input always draws the same row. */
export function phoneRowParts(input: PhoneRowInput): PhoneRowParts {
  const { task, statuses } = input;
  const ctx = { task, statuses, canSelect: input.selectMode, selected: input.selected };
  let tick: PhoneTick = { kind: "none" };
  if (input.selectMode) {
    tick = { kind: "select", label: `Select ${task.title}`, checked: input.selected };
  } else if (input.canComplete) {
    const target = doneTarget(ctx);
    if (target) {
      const done = isDone(ctx);
      tick = { kind: "complete", label: tickLabel(task.title, done), done, toStatusId: target.id };
    }
  }
  const due =
    visibleChips(task, input.shownFields, input.nowMs).find((chip) => chip.key === "due") ?? null;
  return {
    tick,
    title: task.title,
    project: projectChipText(input.path ?? []),
    due,
    status: statuses.find((s) => s.id === task.status_id) ?? null,
  };
}
