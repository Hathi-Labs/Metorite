/**
 * SP-9 (`project_management_app.md` §13.3 "Roll-ups") — the words of the
 * roll-up line on the task panel of an epic.
 *
 * The gateway computes the numbers over EVERY descendant that the reader can
 * see (`GET /projects/tasks/{id}` → `rollup`). This file only words them. The
 * board card keeps its own count of direct children and is not touched here.
 */
import { durationLabel } from "@/lib/taskCard";

export interface EpicRollup {
  slices: { done: number; total: number };
  estimate_mins: { done: number; total: number };
  waits: {
    count: number;
    items: {
      run_id: string;
      task_id: string;
      task_number: number | null;
      title: string;
      person: string | null;
      since: string | null;
    }[];
  };
  hidden: number;
  // Null until their sources land: the appetite (SP-8) and the engine (PJP-6).
  spend: unknown;
  forecast: unknown;
  health: unknown;
  hill: unknown;
}

function minutes(mins: number): string {
  return mins > 0 ? durationLabel(mins) : "0h";
}

/** The parts of the line, in order: progress, waits, hidden. A part with
 *  nothing to say is left out, so "no waits" draws no words. */
export function rollupParts(rollup: EpicRollup): string[] {
  const { slices, estimate_mins: est, waits, hidden } = rollup;
  const parts: string[] = [];
  if (slices.total === 0) {
    parts.push("No slices yet");
  } else {
    const noun = slices.total === 1 ? "slice" : "slices";
    let progress = `${slices.done} of ${slices.total} ${noun} done`;
    if (est.total > 0) progress += ` · ${minutes(est.done)} of ${minutes(est.total)}`;
    parts.push(progress);
  }
  if (waits.count > 0) {
    parts.push(waits.count === 1 ? "1 waits on a person" : `${waits.count} wait on a person`);
  }
  if (hidden > 0) parts.push(`${hidden} hidden from you`);
  return parts;
}

export function rollupLine(rollup: EpicRollup): string {
  return rollupParts(rollup).join(" · ");
}
