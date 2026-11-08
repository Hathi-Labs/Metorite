/**
 * The chip "Waiting for Priya" on the task card (WS-AP AP-9a,
 * `waiting_on_me.md` §5.3 and §6.5).
 *
 * The pure rules live here, and `components/DecisionPanel.tsx` draws them. The
 * chip reads the ONE task read (`GET /projects/decisions?task_id=`):
 *
 * - The addressee reads an open row of `decisions`, and the chip says
 *   "Needs you".
 * - Another reader reads `waiting_on`. One entry says "Waiting for Priya".
 *   Two entries say "Waiting for 2". None says nothing.
 *
 * The chip shows no count for a person (V6) and never the question (ALN-2b).
 * The colour is the one of an agent that awaits (`AGENT_STATES.awaiting`), so
 * no new colour exists.
 */

import { type TaskDecisions, stateNow } from "./decisions";

export interface WaitChip {
  kind: "needs_you" | "waiting";
  text: string;
}

/** The first word of a name, so the chip stays short. An address gives its local part. */
export function shortName(label: string): string {
  const clean = label.trim();
  if (!clean) return "";
  if (clean.includes("@")) return clean.split("@")[0];
  return clean.split(/\s+/)[0];
}

/** The chip for a task, or `null` when nothing waits. */
export function waitChip(
  data: TaskDecisions | null,
  nameOf: (email: string) => string,
  nowMs: number,
): WaitChip | null {
  // asks is null when WAITING_ORGS is off for the org: no chip, the card stays as it was.
  if (!data || data.asks === null) return null;
  if (data.decisions.some((row) => stateNow(row, nowMs) === "open")) {
    return { kind: "needs_you", text: "Needs you" };
  }
  const waiting = data.waiting_on;
  if (waiting.length === 0) return null;
  if (waiting.length === 1) {
    return { kind: "waiting", text: `Waiting for ${shortName(nameOf(waiting[0].decider))}` };
  }
  return { kind: "waiting", text: `Waiting for ${waiting.length}` };
}
