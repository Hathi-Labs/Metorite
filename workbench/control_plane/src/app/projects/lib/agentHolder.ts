/**
 * The holder line of a task that an AI session holds (SW-5,
 * `agent_coordination.md` slice SW-5).
 *
 * "Worked by Claude Code for Priya Rao since 09:14". The facts are the
 * `held_by` object that the gateway puts on each task row (`held_by_of` in
 * `routes/projects/filters.py`): the member, the client and the start of the
 * hold. This file only words them. The card and the task panel draw the line.
 * Fence: `agentHolder.test.ts`.
 */

import { clientName } from "./agentRuns";

/** The `held_by` of a task row. Every field but `member` may be absent. */
export interface TaskHeldBy {
  member: string;
  member_name?: string | null;
  client_via?: string | null;
  client_name?: string | null;
  since?: string | null;
  last_activity_at?: string | null;
  /** The LEASE ended (hours of silence). The next claim may take the task over. */
  stale?: boolean;
}

/**
 * The time of "since". The clock time for a hold of today, else the day and
 * the month. *timeZone* is for a test. The page leaves it out, so the member
 * reads their own clock. A stamp that does not parse gives null.
 */
export function sinceText(
  iso: string | null | undefined,
  nowMs: number = Date.now(),
  timeZone?: string,
): string | null {
  const at = Date.parse(iso ?? "");
  if (Number.isNaN(at)) return null;
  const day = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone });
  if (day.format(at) !== day.format(nowMs)) return day.format(at);
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone,
  }).format(at);
}

/**
 * "Worked by <client> for <member> since <time>". *labels* maps an address to
 * a display name, as the pill does. The gateway's own `member_name` comes
 * next, then the address. A hold with no start time drops "since".
 */
export function holderLine(
  held: TaskHeldBy,
  labels?: ReadonlyMap<string, string>,
  nowMs: number = Date.now(),
  timeZone?: string,
): string {
  const who =
    labels?.get(held.member.trim().toLowerCase()) || held.member_name?.trim() || held.member;
  const client = held.client_name?.trim() || clientName(held.client_via);
  const since = sinceText(held.since, nowMs, timeZone);
  return `Worked by ${client} for ${who}${since ? ` since ${since}` : ""}`;
}
