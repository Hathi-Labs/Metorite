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
 *
 * The card in the chat (STU-B2c-2c, `components/studio/StudioToolCards.tsx`)
 * uses the same client: `readRequest` for its request, and `decideRequest`
 * for the answer.
 */

import type { AppRequest, AppRequestDecision, AppRequestState } from "@/lib/appRequest";

/** Under the agents prefix, so a refresh of the agents view reads it again. */
export const APP_REQUESTS_KEY = "agents:requests";

/**
 * The cache key of one request (STU-B2c-2c). It starts with
 * {@link APP_REQUESTS_KEY}, so a decision that drops the list key drops it
 * too, and the card in the chat reads the request again.
 */
export function requestKey(requestId: string): string {
  return `${APP_REQUESTS_KEY}:${requestId}`;
}

export class AppRequestError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "AppRequestError";
    this.status = status;
  }
}

/** The newest requests that wait, and how many more the page leaves out. */
export interface WaitingList {
  rows: AppRequest[];
  more: number;
}

/**
 * The requests that wait for the member. `null` when the store is dark for
 * this org: `APP_REQUESTS_ORGS` does not hold it, or agent access is off.
 */
export async function listRequests(): Promise<WaitingList | null> {
  const res = await fetch("/api/agent-access/requests", { cache: "no-store" });
  // 404: the store is dark here. Not an error to show.
  if (res.status === 404) return null;
  if (!res.ok) throw new AppRequestError("The list did not load. Try again.", res.status);
  const body = (await res.json().catch(() => ({}))) as { rows?: AppRequest[]; more?: number };
  const rows = Array.isArray(body.rows) ? body.rows : [];
  const more = typeof body.more === "number" && body.more > 0 ? Math.floor(body.more) : 0;
  return { rows, more: rows.length ? more : 0 };
}

/**
 * One request of the member, for the card in the chat (STU-B2c-2c). `null`
 * when the gateway answers 404: the request is not the member's, or the
 * store is dark for this org. The card then draws nothing.
 */
export async function readRequest(requestId: string): Promise<AppRequest | null> {
  const res = await fetch(`/api/agent-access/requests/${encodeURIComponent(requestId)}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new AppRequestError("The approval did not load. Try again.", res.status);
  const body = (await res.json().catch(() => null)) as AppRequest | null;
  if (!body || typeof body !== "object" || body.request_id !== requestId) {
    throw new AppRequestError("The approval did not load. Try again.", res.status);
  }
  return body;
}

/** What the list draws. */
export type WaitingView =
  | { kind: "none" }
  | { kind: "error"; error: string }
  | { kind: "list"; rows: AppRequest[]; more: number };

/**
 * What "Waiting for you" draws, from the cached read and the ids that the
 * member answered here (fix round 1). When no request waits, it draws no
 * section at all: while it loads, while the store is dark (404), for an
 * empty list, and once each row has an answer. The coordinator recommended
 * it on 2026-11-04, and the owner may change it at the preview. A list that
 * fails with nothing to show keeps its error, so the member can try again.
 */
export function waitingView(
  read: { data: WaitingList | null | undefined; error: string | null },
  answered: ReadonlySet<string>,
): WaitingView {
  if (read.data === null) return { kind: "none" };
  if (read.data === undefined) return read.error ? { kind: "error", error: read.error } : { kind: "none" };
  const rows = read.data.rows.filter((r) => !answered.has(r.request_id));
  return rows.length ? { kind: "list", rows, more: read.data.more } : { kind: "none" };
}

/** The line under a list that leaves rows out, or "" when it leaves none. */
export function moreLine(more: number): string {
  if (more <= 0) return "";
  return more === 1
    ? "1 more request waits. Answer these first, and it shows here."
    : `${more} more requests wait. Answer these first, and they show here.`;
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
