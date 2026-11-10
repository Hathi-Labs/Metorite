/**
 * "Ask an agent" — a member starts a run for one of their own connected
 * agents (WS-55 EA-12d, `external_agent_access.md` §12.1).
 *
 * The member picks a live connection and writes the instruction. The
 * gateway makes a `created` run with that `prompt`, and the pill reads
 * "Queued for <client>". The agent finds the run with
 * `list_agent_runs(needs_agent=true)` at the start of its next session.
 *
 * These are the pure rules. `AskAgentDialog.tsx` only draws them, so
 * `askAgent.test.ts` runs each rule in the node-env vitest.
 */

import { agentClaim, agentHost, type AgentConnection } from "@/app/integrations/lib/agents";

import type { AgentRun, AgentStartBody, AgentStartResult } from "./agentRuns";
import { isOpenRun, newIdempotencyKey } from "./agentRuns";
import { ProjectsApiError } from "./api";

/** Where a member connects an agent: the Integrations "AI agents" view (EA-4b). */
export const CONNECT_AGENT_HREF = "/integrations?view=agents";

/** The scope an agent needs to pick a run up. A read-only agent cannot move it. */
export const RUN_WRITE_SCOPE = "projects:write";

/** The longest instruction. The gateway refuses a longer `body` (§12.1). */
export const MAX_INSTRUCTION = 20_000;

/**
 * The most tasks that one "Ask an agent" may start (WS-55 EA-13e, EA3-43). The
 * bulk bar refuses a 21st task, as the bulk edit of the gateway does for its
 * own cap: twenty requests in a row is one person's ask, and a hundred is a
 * queue that the agent would find at the start of its next session.
 */
export const MAX_BULK_ASK = 20;

/** The words of the dialog, in one place. */
export const ASK_COPY = {
  open: "Ask an agent",
  title: "Ask an agent",
  description:
    "Your agent finds this request at the start of its next session, then works on this task.",
  agentLabel: "Agent",
  instructionLabel: "Instruction",
  instructionPlaceholder: "Tell the agent what to do on this task…",
  send: "Send to agent",
  sending: "Sending…",
  loading: "Loading your agents…",
  empty: "You have no connected agent that can work on tasks.",
  emptyHint: "Connect one in Integrations, then ask it here.",
  connect: "Connect an agent",
  listFailed: "Could not load your agents. Close this and try again.",
  sentOne: "Request sent.",
  done: "Done",
} as const;

/** The refusal of a selection past the cap. */
export function bulkLimitText(count: number): string {
  return `Select at most ${MAX_BULK_ASK} tasks. You selected ${count}.`;
}

/** The label of the dialog's description for a selection. */
export function bulkDescription(count: number): string {
  return count === 1
    ? ASK_COPY.description
    : `Your agent finds these ${count} requests at the start of its next session, then works on each task.`;
}

/** A task that the dialog asks about: its id, and its title for the result row. */
export interface AskTarget {
  id: string;
  title?: string | null;
}

/** The result of one task of a bulk ask, for its own row. */
export type AskOutcome =
  | { taskId: string; ok: true; result: AgentStartResult }
  | { taskId: string; ok: false; error: string };

/**
 * Start one run for each task, one request after another, each with its OWN
 * idempotency key (EA-12d's rule, for each task). A refusal of one task stays
 * on that task's row: a 409 shows the server's own text ("The agent has not
 * picked up its last request yet"), and the other tasks still go.
 *
 * Refuses a selection past :data:`MAX_BULK_ASK` by sending NOTHING, so a bar
 * that forgot to check the cap cannot send a 21st request.
 */
export async function startMany(
  targets: readonly AskTarget[],
  connectionId: string | null | undefined,
  text: string,
  start: (taskId: string, body: AgentStartBody) => Promise<AgentStartResult>,
): Promise<AskOutcome[]> {
  if (targets.length === 0 || targets.length > MAX_BULK_ASK) return [];
  const outcomes: AskOutcome[] = [];
  for (const target of targets) {
    const payload = startPayload(connectionId, text);
    if (!payload) {
      outcomes.push({ taskId: target.id, ok: false, error: startFailure(422, undefined) });
      continue;
    }
    try {
      outcomes.push({ taskId: target.id, ok: true, result: await start(target.id, payload) });
    } catch (err) {
      const status = err instanceof ProjectsApiError ? err.status : undefined;
      outcomes.push({
        taskId: target.id,
        ok: false,
        error: startFailure(status, err instanceof Error ? err.message : undefined),
      });
    }
  }
  return outcomes;
}

/** "3 requests sent. 1 task needs a look." for the line above the rows. */
export function outcomeLine(outcomes: readonly AskOutcome[]): string {
  const sent = outcomes.filter((o) => o.ok).length;
  const failed = outcomes.length - sent;
  const requests = `${sent} request${sent === 1 ? "" : "s"} sent.`;
  return failed === 0 ? requests : `${requests} ${failed} task${failed === 1 ? "" : "s"} need${failed === 1 ? "s" : ""} a look.`;
}

/**
 * The connections a member can ask: live rows that may write to Projects.
 * A connection with `projects:read` only can find the run, but it can never
 * move it out of `queued`, so it is not offered.
 */
export function askableConnections(
  rows: readonly AgentConnection[] | null | undefined,
): AgentConnection[] {
  return (rows ?? []).filter((row) => (row.scopes ?? []).includes(RUN_WRITE_SCOPE));
}

/**
 * The label of one connection in the picker: the verified host, then the
 * name the agent gives itself as a claim. Two connections of one client
 * differ by their dates, so the label adds the day it was connected.
 */
export function connectionLabel(
  row: AgentConnection,
  all: readonly AgentConnection[] = [row],
): string {
  const host = agentHost(row);
  const name = (row.client_name || "").trim();
  const base = agentClaim(row) && name ? `${name} (${host})` : host;
  const twins = all.filter((other) => agentHost(other) === host).length;
  if (twins < 2) return base;
  const day = (row.created_at || "").slice(0, 10);
  return day ? `${base}, connected ${day}` : base;
}

/** The instruction as the gateway gets it, or null when it is empty. */
export function cleanInstruction(text: string): string | null {
  const cleaned = text.replace(/\u0000/g, "").trim();
  return cleaned ? cleaned : null;
}

/**
 * The start body, or null when it is not ready. Each send takes a fresh
 * `idempotency_key`, so a retry after a failure is a new request.
 */
export function startPayload(
  connectionId: string | null | undefined,
  text: string,
  idempotencyKey: string = newIdempotencyKey(),
): AgentStartBody | null {
  const body = cleanInstruction(text);
  if (!connectionId || !body || body.length > MAX_INSTRUCTION) return null;
  return { connection_id: connectionId, body, idempotency_key: idempotencyKey };
}

/**
 * The words after a start fails. A 409 means the agent already has an
 * open run here that it has not picked up. The server's text says that, so
 * the dialog shows it as it is, with no "Try again".
 */
export function startFailure(status: number | undefined, message: string | undefined): string {
  const why = message?.trim() ?? "";
  if (status === 409) return why || "The agent has not picked up its last request yet.";
  if (status === 422) return why || "Pick one of your agents and write an instruction.";
  return `Could not send the request.${why ? ` ${why}` : ""} Try again.`;
}

/**
 * Does the caller already have an open run on this task? Then the task
 * offers no second "Ask an agent": the open run is where the member talks
 * to the agent.
 */
export function hasOpenRunOf(runs: readonly AgentRun[], me: string | null | undefined): boolean {
  const who = (me ?? "").trim().toLowerCase();
  if (!who) return false;
  return runs.some((run) => isOpenRun(run) && (run.member_email ?? "").toLowerCase() === who);
}
