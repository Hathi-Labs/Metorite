// ── Box tokens in Integrations, AI agents, as pure decisions ───────────────
//
// WS-55 EA-19d. Spec: `project-docs/specs/external_agent_access.md` §10.9
// (questions 3, 4 and 5) and §15. `components/BoxConnect.tsx` only draws what
// this module decides, so `box.test.ts` runs every rule in the node-env vitest.
//
// Four BFF routes (`app/api/agent-access/box-tokens/relay.ts`):
//
//     POST /api/agent-access/box-tokens/check            the one-time ticket
//     POST /api/agent-access/box-tokens                  mint, spends the ticket
//     POST /api/agent-access/box-tokens/<id>/rotate      rotate, spends a ticket
//     POST /api/agent-access/box-tokens/revoke-all       an admin revokes all
//
// ⚠️ The mint and the rotate answer the raw token ONCE. This module hands it
// to the caller and keeps no copy. It never goes in a URL, in storage, in a
// log or in a cached list. The caller holds it in dialog state and drops it
// when the dialog closes.
//
// The box flag `agent_box_tokens_enabled` ships off. With it off, check, mint
// and rotate answer 404. The flow turns that into `{ ok: false, off: true }`
// and never throws on it.

import { OFFLINE_ACCESS, READ_SCOPE, scopeRows, type ScopeRow } from "@/app/connect/authorize/consent";

import type { AgentConnection } from "./agents";

/** The default and the largest lifetime of a box token, in days (§10.9 q. 4). */
export const BOX_DAYS_DEFAULT = 90;
export const BOX_DAYS_MAX = 365;
export const BOX_NAME_MAX = 100;

/** The line the page shows when the flag is off (the 404 of check or mint). */
export const BOX_OFF_MESSAGE = "Box tokens are not turned on for your organization.";

/** The connect command. EA-19e builds the script. It asks for the token in a hidden prompt. */
export const BOX_CONNECT_COMMAND = "bash scripts/box_connect.sh";
export const BOX_CONNECT_HINT = "The script asks for the token in a hidden prompt. Paste the token there.";
export const BOX_TOKEN_WARNING = "Copy the token now. It cannot be shown again.";

export const WRITE_SCOPE = "projects:write";

/**
 * The scope switches of the dialog: the scopes that the consent copy lists
 * (`SCOPE_COPY`), without `offline_access`, because a box has no refresh token.
 */
export function boxScopeRows(): ScopeRow[] {
  return scopeRows([READ_SCOPE, WRITE_SCOPE, "context:read"]).filter((r) => r.scope !== OFFLINE_ACCESS);
}

/** The scopes a new dialog starts with (§10.9 q. 2). */
export const DEFAULT_BOX_SCOPES: readonly string[] = [READ_SCOPE, WRITE_SCOPE];

/**
 * Turn one scope on or off. Read is needed for the others, so turning it off
 * clears them, and turning another one on also turns read on. Same rule as
 * `toggleScope` of the consent page.
 */
export function toggleBoxScope(chosen: readonly string[], scope: string): string[] {
  const next = new Set(chosen);
  if (next.has(scope)) {
    next.delete(scope);
    if (scope === READ_SCOPE) next.clear();
  } else {
    next.add(scope);
    next.add(READ_SCOPE);
  }
  return boxScopeRows()
    .map((r) => r.scope)
    .filter((s) => next.has(s));
}

export interface BoxForm {
  name: string;
  scopes: readonly string[];
  days: number;
  includePersonal: boolean;
}

export const EMPTY_BOX_FORM: BoxForm = {
  name: "",
  scopes: DEFAULT_BOX_SCOPES,
  days: BOX_DAYS_DEFAULT,
  includePersonal: false,
};

/** The first thing wrong with a name, or `null`. The gateway checks it again. */
export function nameProblem(name: string): string | null {
  const n = name.trim();
  if (n.length === 0) return "Give the box a name.";
  if (n.length > BOX_NAME_MAX) return `Use ${BOX_NAME_MAX} characters or fewer.`;
  return null;
}

export function daysProblem(days: number): string | null {
  if (!Number.isInteger(days) || days < 1 || days > BOX_DAYS_MAX) {
    return `Choose from 1 to ${BOX_DAYS_MAX} days.`;
  }
  return null;
}

/** The first thing wrong with the form, or `null` when it can be sent. */
export function formProblem(form: BoxForm): string | null {
  return (
    nameProblem(form.name) ??
    daysProblem(form.days) ??
    (form.scopes.includes(READ_SCOPE) ? null : "Choose a scope.")
  );
}

/** The body of the check and of the mint (without the ticket). */
export function boxBody(form: BoxForm) {
  return {
    name: form.name.trim(),
    scopes: [...form.scopes],
    days: form.days,
    include_personal: form.includePersonal,
  };
}

// ── One row ────────────────────────────────────────────────────────────────

export const DAY_MS = 86_400_000;
export const AMBER_DAYS = 14;
export const RED_DAYS = 3;
export const NEW_ADDRESS_MS = DAY_MS;

export function isBox(row: Pick<AgentConnection, "credential">): boolean {
  return row.credential === "box";
}

/** `Last used` for a box. An OAuth row stamps a token refresh only. */
export function lastUsedLabel(row: Pick<AgentConnection, "credential">): string {
  return isBox(row) ? "Last used" : "Last token refresh";
}

export type ExpiryColor = "amber" | "red" | null;

export interface ExpiryView {
  /** A `statusAccent` colour name, or `null` for the quiet default. */
  color: ExpiryColor;
  text: string;
}

/** "Expires in N days": amber from 14 days out, red from 3 days out (§10.9 q. 4). */
export function expiryView(expiresAt: string | null | undefined, now: number = Date.now()): ExpiryView {
  const t = expiresAt ? Date.parse(expiresAt) : NaN;
  if (Number.isNaN(t)) return { color: null, text: "" };
  const ms = t - now;
  if (ms <= 0) return { color: "red", text: "Expired" };
  const days = Math.ceil(ms / DAY_MS);
  const text = days === 1 ? "Expires in 1 day" : `Expires in ${days} days`;
  if (days <= RED_DAYS) return { color: "red", text };
  if (days <= AMBER_DAYS) return { color: "amber", text };
  return { color: null, text };
}

export const NEW_ADDRESS_HINT = 'Use "Rotate now" or Revoke if this was not your box';

/** The new-address mark: the last seen address, when it changed in the last 24 hours. */
export function newAddress(
  row: Pick<AgentConnection, "credential" | "last_seen_addr" | "addr_changed_at">,
  now: number = Date.now(),
): { addr: string; hint: string } | null {
  if (!isBox(row) || !row.addr_changed_at) return null;
  const t = Date.parse(row.addr_changed_at);
  if (Number.isNaN(t) || now - t > NEW_ADDRESS_MS || t - now > 60_000) return null;
  return { addr: row.last_seen_addr || "unknown address", hint: NEW_ADDRESS_HINT };
}

export const ROTATE_TIP = "Make a new token. The old token works for 24 more hours.";
export const ROTATE_NOW_TIP = "Make a new token. The old token stops at once.";

/** An admin sees "Revoke all box tokens" when the org list holds a box. A member never does. */
export function showRevokeAll(canManageOrg: boolean, rows: readonly AgentConnection[] | null | undefined): boolean {
  return canManageOrg && (rows ?? []).some(isBox);
}

export function revokeAllCopy(): { title: string; body: string } {
  return {
    title: "Revoke all box tokens?",
    body:
      "Every box token of your organization stops at once. Each box must get a new token. " +
      "Other agent connections stay.",
  };
}

// ── The requests ───────────────────────────────────────────────────────────

/** What a mint or a rotate answers. The `token` is shown once and never kept. */
export interface IssuedBoxToken {
  token: string;
  connection_id: string;
  name: string;
  scopes: string[];
  expires_at: string;
}

export type BoxResult<T> = { ok: true; value: T } | { ok: false; off: boolean; message: string };

const FAILED = "The token was not made. Try again.";

async function readJson(res: Response): Promise<Record<string, unknown>> {
  const body = (await res.json().catch(() => ({}))) as unknown;
  return body && typeof body === "object" ? (body as Record<string, unknown>) : {};
}

/** The words of a refusal: the gateway's own description, else a plain line. */
function refusal(res: Response, body: Record<string, unknown>): { ok: false; off: boolean; message: string } {
  if (res.status === 404) return { ok: false, off: true, message: BOX_OFF_MESSAGE };
  const detail = body.detail;
  const inner = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : body;
  const words = inner.error_description ?? inner.error;
  return { ok: false, off: false, message: typeof words === "string" && words ? words : `${FAILED} (${res.status})` };
}

async function post(path: string, body: unknown): Promise<{ res: Response; json: Record<string, unknown> }> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  return { res, json: await readJson(res) };
}

function isIssued(v: Record<string, unknown>): v is Record<string, unknown> & IssuedBoxToken {
  return typeof v.token === "string" && v.token.length > 0 && typeof v.connection_id === "string";
}

/** Check, then mint with the ticket. */
export async function mintBoxToken(form: BoxForm): Promise<BoxResult<IssuedBoxToken>> {
  const body = boxBody(form);
  try {
    const check = await post("/api/agent-access/box-tokens/check", body);
    if (!check.res.ok) return refusal(check.res, check.json);
    if (typeof check.json.ticket !== "string") return { ok: false, off: false, message: FAILED };
    const mint = await post("/api/agent-access/box-tokens", { ...body, ticket: check.json.ticket });
    if (!mint.res.ok) return refusal(mint.res, mint.json);
    if (!isIssued(mint.json)) return { ok: false, off: false, message: FAILED };
    return { ok: true, value: mint.json };
  } catch {
    return { ok: false, off: false, message: FAILED };
  }
}

/** Check, then rotate with the ticket. `now` kills the old token at once. */
export async function rotateBoxToken(
  connectionId: string,
  opts: { days?: number; now: boolean },
): Promise<BoxResult<IssuedBoxToken>> {
  const days = opts.days ?? BOX_DAYS_DEFAULT;
  const base = { days, now: opts.now };
  try {
    const check = await post("/api/agent-access/box-tokens/check", { ...base, connection_id: connectionId });
    if (!check.res.ok) return refusal(check.res, check.json);
    if (typeof check.json.ticket !== "string") return { ok: false, off: false, message: FAILED };
    const rotate = await post(`/api/agent-access/box-tokens/${encodeURIComponent(connectionId)}/rotate`, {
      ...base,
      ticket: check.json.ticket,
    });
    if (!rotate.res.ok) return refusal(rotate.res, rotate.json);
    if (!isIssued(rotate.json)) return { ok: false, off: false, message: FAILED };
    return { ok: true, value: rotate.json };
  } catch {
    return { ok: false, off: false, message: FAILED };
  }
}

/** An admin revokes every box token. Stays live with the flag off. Gives the count. */
export async function revokeAllBoxTokens(): Promise<BoxResult<number>> {
  try {
    const { res, json } = await post("/api/agent-access/box-tokens/revoke-all", {});
    if (!res.ok) return refusal(res, json);
    return { ok: true, value: typeof json.revoked === "number" ? json.revoked : 0 };
  } catch {
    return { ok: false, off: false, message: "The box tokens were not revoked. Try again." };
  }
}

/** "3 box tokens revoked", "1 box token revoked", "No box tokens to revoke". */
export function revokedText(count: number): string {
  if (count === 0) return "No box tokens to revoke";
  return count === 1 ? "1 box token revoked" : `${count} box tokens revoked`;
}
