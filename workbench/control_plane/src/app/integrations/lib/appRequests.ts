/**
 * The list "Waiting for you" of Integrations, AI agents (WS-MCP MCP-P1b-1).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §19.5 ("The member
 * routes") and MCP-Q3 of §19.14. The client of the three BFF routes:
 *
 *     GET  /api/agent-access/requests                  the requests that wait
 *     GET  /api/agent-access/requests/<id>             one request
 *     POST /api/agent-access/requests/<id>/decision    {decision, idempotency_key}
 *
 * The member and the org come from the session at the gateway. The BFF rules
 * are in `app/api/agent-access/requests/relay.ts`. Fence:
 * `app/api/agent-access/requests/relay.test.ts`.
 */

import type { AppRequest, AppRequestDecision, AppRequestState } from "@/lib/appRequest";

/** Under the agents prefix, so a refresh of the agents view reads it again. */
export const APP_REQUESTS_KEY = "agents:requests";

export class AppRequestError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "AppRequestError";
    this.status = status;
  }
}

/** The requests that wait for the member. `null` when agent access is off. */
export async function listRequests(): Promise<AppRequest[] | null> {
  const res = await fetch("/api/agent-access/requests", { cache: "no-store" });
  // 404: agent access is off for this deployment. Not an error to show.
  if (res.status === 404) return null;
  if (!res.ok) throw new AppRequestError("The list did not load. Try again.", res.status);
  const body = (await res.json().catch(() => ({}))) as { rows?: AppRequest[] };
  return Array.isArray(body.rows) ? body.rows : [];
}

/** The words of a refused decision: the server's sentence when it gives one. */
export function refusalText(status: number, detail: unknown): string {
  const message = (detail as { message?: unknown } | null)?.message;
  if (typeof message === "string" && message) return message;
  if (status === 404) return "This request is gone. Refresh the list.";
  if (status === 409) return "This request already has an answer.";
  return "Your answer was not saved. Try again.";
}

/** Send the member's answer. One key for each press, so a retry of the same
 *  press gives the first answer, and a second press never decides twice. */
export async function decideRequest(
  requestId: string,
  decision: AppRequestDecision,
  idempotencyKey: string,
): Promise<{ state: AppRequestState }> {
  const res = await fetch(`/api/agent-access/requests/${encodeURIComponent(requestId)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, idempotency_key: idempotencyKey }),
  });
  const body = (await res.json().catch(() => ({}))) as { state?: AppRequestState; detail?: unknown };
  if (!res.ok) throw new AppRequestError(refusalText(res.status, body.detail), res.status);
  return { state: body.state ?? (decision === "approve" ? "approved" : "declined") };
}

/** The toast of a decision that landed. */
export function decisionToast(decision: AppRequestDecision, request: AppRequest): { title: string; description: string } {
  const what = request.card.title || request.op || "the change";
  return decision === "approve"
    ? { title: "Approved", description: `The agent can now make this change once: ${what}` }
    : { title: "Declined", description: `Nothing changes: ${what}` };
}
