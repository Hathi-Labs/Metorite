/**
 * WS-MCP SW-23 — what the task panel says about the Check of a task.
 *
 * A Check is a read-only command that proves the work is still open. An
 * agent runs it and reports `open`, `passed` or `cannot_run`
 * (`gateway/routes/projects/checks.py`). The panel only reads it: the
 * member sees the command, the last result and the day it last ran.
 *
 * Pure, so `taskCheck.test.ts` holds each word. The tone is a `Badge` tone,
 * so no colour is named here.
 */
import type { BadgeTone } from "@/components/ui/Badge";

import type { CheckResult, TaskCheck } from "./api";

/** The badge of each result. One label per result, on every surface. */
export const CHECK_RESULT_LOOK: Record<CheckResult, { label: string; tone: BadgeTone; icon: string }> = {
  passed: { label: "Passed", tone: "success", icon: "CircleCheck" },
  open: { label: "Still open", tone: "warning", icon: "CircleDot" },
  cannot_run: { label: "Could not run", tone: "neutral", icon: "CircleSlash" },
};

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

/**
 * "12 Nov 2026", on the reader's own day. The value is an instant, not a
 * floating date, so the reader's clock decides the day. The month is a
 * word, so a reader in India and one in the US read the same day.
 */
export function runDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return `${at.getDate()} ${MONTHS[at.getMonth()]} ${at.getFullYear()}`;
}

export interface CheckSummary {
  /** The badge, or `null` when no run was reported yet. */
  result: { label: string; tone: BadgeTone; icon: string } | null;
  /** One sentence: when it last ran, and whether a run is due. */
  when: string;
}

/** The words of one Check, for the panel. */
export function checkSummary(check: TaskCheck): CheckSummary {
  const result = check.last_result ? CHECK_RESULT_LOOK[check.last_result] ?? null : null;
  const day = runDay(check.last_run_at);
  if (!day) {
    return { result, when: "Not run yet. An agent runs it next." };
  }
  const due = check.due ? " A new run is due." : "";
  return { result, when: `Last run ${day}.${due}` };
}
