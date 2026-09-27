/**
 * WS-27bn R3d — the Pulse panel's words, and the reads of one card.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R3d.
 *
 * ⚠️ **The server decides. This file does not.** The status, the pill, the
 * reasons, the counts and the hours arrive from `analytics_pulse.py`
 * `pulse_body`. The one subtraction here is the "…and N more" of the focus
 * list, from the server's `focus_total` and the rows on screen, through
 * `moreNote`. It is a display count, as a bar width is.
 *
 * ⚠️ **The HR half is decided PER ROW, by key presence** (edit E4). An
 * admin reads the HR half of every card. A member reads it on their own
 * card only. So the section's `hr_visible` cannot decide it, and
 * {@link hasHrHalf} reads the row.
 *
 * ⚠️ **"On leave" is never "Idle".** A person away today has room on no
 * plan, so the status replaces the pill on screen, and it wears the
 * neutral hue.
 */
import { PILL_HUE, PILL_LABEL, hours } from "@/app/people/lib/dashboard";
import {
  HELP_REASON_WORDS,
  PULSE_STATUS_WORDS,
  hiddenPeopleLine,
} from "@/lib/reportEmail";
import type { AccentHue } from "@/lib/statusAccent";

import type { PulseFocusTask, PulseReport, PulseRow } from "./api";
import { asList } from "./analyticsRead";
import { moreNote } from "./hygiene";

export { hiddenPeopleLine };

/** How many focus tasks a card names. The folded table names them all. */
export const FOCUS_SHOWN = 3;

/** The hue of a card for a person on leave today. */
export const ON_LEAVE_HUE: AccentHue = "gray";

/** A help reason, in words. The email and the chat card say the same. */
export const HELP_REASON_LABEL: Readonly<Record<string, string>> = HELP_REASON_WORDS;

/** The rows, read at the boundary. A malformed entry is dropped, not drawn. */
export function pulseRows(data: PulseReport | null | undefined): PulseRow[] {
  return asList<PulseRow>(data?.rows).filter(
    (row): row is PulseRow =>
      !!row && typeof row === "object" && typeof row.assignee === "string"
  );
}

/** Who a card is, as a person reads it. */
export function pulseName(row: PulseRow): string {
  return row.name || row.assignee;
}

/** Whether the server sent this row's HR half. It reads the row, not the body. */
export function hasHrHalf(row: PulseRow): boolean {
  return typeof row.status === "string";
}

/**
 * The status of a card, with its hue and its words, or null for a row
 * without its HR half. `on_leave` wins over the pill.
 */
export function statusMark(
  row: PulseRow
): { label: string; hue: AccentHue; title: string } | null {
  if (!hasHrHalf(row)) return null;
  if (row.status === "on_leave") {
    return {
      label: PULSE_STATUS_WORDS.on_leave,
      hue: ON_LEAVE_HUE,
      title: "On leave today, so this person has no room for more work.",
    };
  }
  const pill = row.pill;
  if (!pill || !(pill in PILL_HUE)) return null;
  return {
    label: PILL_LABEL[pill],
    hue: PILL_HUE[pill],
    title: row.pill_reason || PILL_LABEL[pill],
  };
}

/**
 * The load bar of one card: committed of working hours this week when the
 * row carries them, else overdue of open tasks. Two server figures, and
 * the text states both, because a bar clips above 100 percent.
 */
export function loadBar(row: PulseRow): {
  kind: "hours" | "tasks";
  value: number;
  of: number;
  text: string;
  title: string;
  emptyTitle: string;
} {
  const committed = row.committed_hours_this_week;
  const working = row.working_hours_this_week;
  if (row.hours_basis && typeof committed === "number" && typeof working === "number") {
    return {
      kind: "hours",
      value: committed,
      of: working,
      text: `${hours(committed).replace(/h$/, "")} of ${hours(working)}`,
      title:
        `${hours(committed)} committed of ${hours(working)} working time this week.` +
        " Estimated, never logged.",
      emptyTitle: `No working time this week, and ${hours(committed)} committed.`,
    };
  }
  return {
    kind: "tasks",
    value: row.overdue,
    of: row.open_tasks,
    text: `${row.overdue} of ${row.open_tasks} overdue`,
    title: `${row.overdue} of ${row.open_tasks} open tasks in this scope are overdue.`,
    emptyTitle: "No open tasks in this scope.",
  };
}

/** "Needs help: Blocked, Stale", or null when the card needs no help. */
export function helpLine(row: PulseRow): string | null {
  const reasons = asList<string>(row.help_reasons);
  if (!row.needs_help || reasons.length === 0) return null;
  return `Needs help: ${reasons.map((r) => HELP_REASON_LABEL[r] ?? r).join(", ")}`;
}

/** Every focus task the server sent for one card, in its order. */
export function pulseFocus(row: PulseRow): PulseFocusTask[] {
  return asList<PulseFocusTask>(row.focus).filter(
    (f) => !!f && typeof f === "object" && typeof f.title === "string"
  );
}

/** The focus tasks a card names, in the server's order. */
export function focusShown(row: PulseRow): PulseFocusTask[] {
  return pulseFocus(row).slice(0, FOCUS_SHOWN);
}

/** "…and 4 more", from the server's `focus_total`, or null. */
export function focusMore(row: PulseRow, shown: number): string | null {
  return moreNote(row.focus_total, shown);
}

/** Why a focus task is on the card, in words. */
export function focusWhy(task: PulseFocusTask): string {
  if (task.scheduled_today) return "scheduled today";
  if (task.in_progress) return "in progress";
  return "due soon";
}
