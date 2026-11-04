/**
 * The words and the rows of an app request card (WS-MCP MCP-P1b-1).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §19.5 ("The card") and
 * the decision D8 of the MCP-P1b audit. An outside agent, or a Studio write,
 * asks the member to approve ONE change. The gateway stores the exact call
 * (`gateway/app_requests.py`), and `components/AppRequestCard.tsx` draws it.
 *
 * Two kinds of text, and the card keeps them apart:
 *
 * - **The server's frame.** The title, the class line, the client and the
 *   expiry come from the server: the manifest and the curated client label.
 * - **The agent's values.** Each value of the call is text that the agent
 *   wrote. The card draws each one as plain text, under {@link AGENT_TEXT_MARK},
 *   and shows all of it. The server refuses a call that is too large to show
 *   (413), so the card never cuts a value.
 *
 * Pure: no fetch and no DOM. Fence: `components/AppRequestCard.test.ts`.
 */

import { fieldSpec } from "@/lib/cardFields";

/** The states of a request, as the gateway reads them. */
export type AppRequestState = "awaiting" | "approved" | "declined" | "done" | "expired";

export type AppRequestDecision = "approve" | "decline";

/** The stored call: the exact change that runs after an approve. */
export interface AppRequestCall {
  method?: string;
  template?: string;
  path?: string;
  query?: [string, string][];
  body?: unknown;
  if_match?: string | null;
}

/** The server's words for the card. */
export interface AppRequestCardWords {
  app_name?: string;
  title?: string;
  class_line?: string;
  client?: string;
}

/** One request, as `GET /agent-access/requests` gives it. */
export interface AppRequest {
  request_id: string;
  run_id: string;
  state: AppRequestState;
  decision: AppRequestDecision | null;
  source: "mcp" | "studio" | null;
  app: string;
  subject: string | null;
  op: string | null;
  class: "B" | "C" | null;
  client_via: string | null;
  card: AppRequestCardWords;
  call: AppRequestCall;
  expires_at: string | null;
  created_at: string | null;
  decided_at: string | null;
}

/** The mark over each value that the agent wrote (D8 rule 2). */
export const AGENT_TEXT_MARK = "Text from the agent. Check it.";

/** The title row of the card. The same word as the chat's approval card. */
export const REQUEST_TITLE = "Approval";

/** The line of a request that waits. */
export const REQUEST_HINT = "Nothing changes until you approve.";

/** One row of the payload: a label in product words and a value as text. */
export interface PayloadRow {
  key: string;
  label: string;
  value: string;
}

/** A value as plain text, whole. A string stays as the agent wrote it. */
export function valueText(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

/**
 * The rows of the call, in a fixed order: the target, each query pair, the
 * version, then each field of the body. Every value shows, whole.
 */
export function payloadRows(call: AppRequestCall): PayloadRow[] {
  const rows: PayloadRow[] = [];
  if (call.path) {
    rows.push({ key: "target", label: "Target", value: `${call.method ?? ""} ${call.path}`.trim() });
  }
  for (const [key, value] of call.query ?? []) {
    rows.push({ key: `query:${key}`, label: fieldSpec(key).label, value: valueText(value) });
  }
  if (call.if_match) rows.push({ key: "if_match", label: "Version", value: call.if_match });
  const body = call.body;
  if (body && typeof body === "object" && !Array.isArray(body)) {
    for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
      rows.push({ key, label: fieldSpec(key).label, value: valueText(value) });
    }
  } else if (body !== undefined && body !== null) {
    rows.push({ key: "body", label: "Body", value: valueText(body) });
  }
  return rows;
}

/** The host of a client stamp, `mcp:claude.ai:claude-code` → `claude.ai`.
 *  The rule of `agent_oauth.client_host`. */
export function clientHost(via: string | null | undefined): string {
  const body = (via ?? "").trim().toLowerCase().replace(/^mcp:/, "");
  if (!body || body.startsWith("studio:")) return "";
  return body.split(":").length > 2 ? body : body.split(":")[0];
}

/** Who asks: the curated label, and the verified host beside it. */
export function askerText(request: Pick<AppRequest, "card" | "client_via">): string {
  const label = request.card.client || "An agent";
  const host = clientHost(request.client_via);
  return host && host !== label.toLowerCase() ? `${label} · ${host}` : label;
}

/** The line under the title: who asks to change which app. */
export function askLine(request: Pick<AppRequest, "card" | "client_via" | "app">): string {
  return `${askerText(request)} asks to change ${request.card.app_name || request.app}.`;
}

const STATE_WORDS: Record<AppRequestState, string> = {
  awaiting: "Waiting for you",
  approved: "Approved",
  declined: "Declined",
  done: "Done",
  expired: "Expired",
};

/** The state as a member reads it. */
export function stateWords(state: AppRequestState): string {
  return STATE_WORDS[state] ?? state;
}

const EXPIRY = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

/** True when the request is past its expiry at *now*. A stamp that does not
 *  parse reads as expired, so the card never offers Approve for it. */
export function isExpired(expiresAt: string | null | undefined, now: number): boolean {
  const at = Date.parse(expiresAt ?? "");
  return Number.isNaN(at) || at <= now;
}

/** "Expires 5 Nov, 14:30" in the member's own zone, or "Expired". */
export function expiryText(expiresAt: string | null | undefined, now: number): string {
  if (isExpired(expiresAt, now)) return "Expired";
  return `Expires ${EXPIRY.format(new Date(Date.parse(expiresAt as string)))}`;
}

/** The member may approve or decline: the request waits and is live. */
export function canDecide(request: Pick<AppRequest, "state" | "expires_at">, now: number): boolean {
  return request.state === "awaiting" && !isExpired(request.expires_at, now);
}
