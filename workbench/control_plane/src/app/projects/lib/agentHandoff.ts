/**
 * WS-55 SW-24 (agent_coordination.md): the handoff note of an agent that
 * stops. The server writes it as a `response` activity with
 * `content.handoff`, so the web reads it from the activities of a run that
 * it already loads. Pure helpers only.
 */

import { type AgentActivity, clientName, personName } from "./agentRuns";

/** The four texts of a note, in the order that a reader meets them. */
export const HANDOFF_FIELDS = [
  { key: "done", label: "Done" },
  { key: "left", label: "Left" },
  { key: "evidence", label: "Evidence" },
  { key: "next", label: "Next" },
] as const;

export type HandoffKey = (typeof HANDOFF_FIELDS)[number]["key"];

/** The note of one activity, or null when it has no text. */
export function handoffOf(
  activity: Pick<AgentActivity, "type" | "content">,
): Partial<Record<HandoffKey, string>> | null {
  if (activity.type !== "response") return null;
  const raw = activity.content?.handoff;
  if (!raw || typeof raw !== "object") return null;
  const note: Partial<Record<HandoffKey, string>> = {};
  for (const { key } of HANDOFF_FIELDS) {
    const value = (raw as Record<string, unknown>)[key];
    if (typeof value === "string" && value.trim()) note[key] = value.trim();
  }
  return Object.keys(note).length ? note : null;
}

/** The newest activity that carries a note, or null. `activities` run oldest first. */
export function newestHandoff(activities: readonly AgentActivity[]): AgentActivity | null {
  for (let i = activities.length - 1; i >= 0; i -= 1) {
    if (handoffOf(activities[i])) return activities[i];
  }
  return null;
}

/** "Handed off by Claude Code for Priya Shah": the client, then the member. */
export function handoffByline(
  activity: Pick<AgentActivity, "actor" | "actor_via">,
  labels?: ReadonlyMap<string, string>,
): string {
  const client = clientName(activity.actor_via);
  const member = personName(activity.actor, labels);
  return member ? `Handed off by ${client} for ${member}` : `Handed off by ${client}`;
}
