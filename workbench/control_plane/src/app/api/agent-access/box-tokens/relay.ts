/**
 * The shared half of the four box-token BFF routes (WS-55 EA-19c). Server
 * only.
 *
 * Spec: `project-docs/specs/external_agent_access.md` §10.9 and §15 EA-19c.
 *
 *     POST /api/agent-access/box-tokens/check        → gateway POST /agent-access/box-tokens/check
 *     POST /api/agent-access/box-tokens              → gateway POST /agent-access/box-tokens
 *     POST /api/agent-access/box-tokens/<id>/rotate  → gateway POST /agent-access/box-tokens/<id>/rotate
 *     POST /api/agent-access/box-tokens/revoke-all   → gateway POST /agent-access/box-tokens/revoke-all
 *
 * Four rules, each with a test in `relay.test.ts`:
 *
 * 1. ⚠️ **Same origin only.** A request whose `Sec-Fetch-Site` is not
 *    `same-origin` gets 403, and so does one with no such header
 *    (`sameOrigin` in `lib/accountsServer.ts`). The mint and the rotation
 *    hand out a secret, so this is the CSRF lock of both. The gateway cannot
 *    check it, because it sees only this route's server call (spec §10.5).
 * 2. **The member and the organization come from the session** at the
 *    gateway (R11). The body that leaves holds only the fields of its act:
 *    any other field, such as an org or a member, never reaches the gateway.
 *    A body needs a `Content-Length`, else 411. A declared length over
 *    `MAX_BOX_BODY` gets 413 before the read, and so does a longer body
 *    after it. A body that is not a JSON object gets 400.
 * 3. **Never a 3xx.** The relay does not follow a gateway redirect, and turns
 *    one into a 502.
 * 4. **Never cached.** Each answer carries `Cache-Control: no-store`, because
 *    the mint and the rotation answer the raw token once. A gateway 503 keeps
 *    its `Retry-After`.
 */
import { NextResponse, type NextRequest } from "next/server";
import { refuseBadSegment } from "@/app/api/integrations/safeSegment";
import { sameOrigin } from "@/lib/accountsServer";
import {
  GATEWAY_URL,
  NoIdentityError,
  gatewayFetch,
  gatewayHeaders,
  requireIdentity,
} from "@/lib/gateway";

/** A box body is far under this: two lists of 50 ids and the scopes. */
export const MAX_BOX_BODY = 16_384;

const GATEWAY_TIMEOUT_MS = 10_000;

const MINT_FIELDS = [
  "name",
  "scopes",
  "days",
  "pinned_projects",
  "left_out_projects",
  "include_personal",
] as const;

/** The fields that each act may send (rule 2). */
export const BOX_FIELDS = {
  check: [...MINT_FIELDS, "connection_id", "now"],
  mint: [...MINT_FIELDS, "ticket"],
  rotate: ["days", "now", "ticket"],
} as const;

export type BoxAct = keyof typeof BOX_FIELDS;

function answer(body: string | null, status: number, retryAfter?: string | null): NextResponse {
  const headers: Record<string, string> = { "Cache-Control": "no-store" };
  if (body !== null) headers["Content-Type"] = "application/json";
  if (retryAfter) headers["Retry-After"] = retryAfter;
  return new NextResponse(body, { status, headers });
}

function refuse(error: string, status: number): NextResponse {
  return answer(JSON.stringify({ error }), status);
}

function isRedirectAnswer(res: Response): boolean {
  return res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400);
}

/** The `Content-Length` of *req* as a whole number, or `null` with none. */
function declaredLength(req: NextRequest): number | null {
  const value = req.headers.get("content-length");
  if (value === null || !/^[0-9]{1,9}$/.test(value.trim())) return null;
  return Number(value.trim());
}

/** The body of *act*, with its own fields only, or the answer that refuses it. */
async function pickBody(req: NextRequest, act: BoxAct): Promise<string | NextResponse> {
  const declared = declaredLength(req);
  if (declared === null) return refuse("length_required", 411);
  if (declared > MAX_BOX_BODY) return refuse("too_large", 413);
  const raw = await req.text();
  if (raw.length > MAX_BOX_BODY) return refuse("too_large", 413);
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return refuse("bad_body", 400);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return refuse("bad_body", 400);
  }
  const given = parsed as Record<string, unknown>;
  const picked: Record<string, unknown> = {};
  for (const field of BOX_FIELDS[act]) {
    if (Object.prototype.hasOwnProperty.call(given, field)) picked[field] = given[field];
  }
  return JSON.stringify(picked);
}

async function relay(path: string, body: string | undefined): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return refuse("unauthenticated", 401);
  let headers: Record<string, string>;
  try {
    headers = await gatewayHeaders(body === undefined ? {} : { "Content-Type": "application/json" });
  } catch (err) {
    if (err instanceof NoIdentityError) return refuse("unauthenticated", 401);
    throw err;
  }
  let res: Response;
  try {
    res = await gatewayFetch(`${GATEWAY_URL}${path}`, {
      method: "POST",
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(GATEWAY_TIMEOUT_MS),
    });
  } catch {
    return refuse("unavailable", 502);
  }
  if (isRedirectAnswer(res)) return refuse("bad_gateway", 502);
  return answer(await res.text(), res.status, res.headers.get("retry-after"));
}

/** The check, the mint or the rotation of *id*. Same origin only (rule 1). */
export async function relayBox(req: NextRequest, act: BoxAct, id?: string): Promise<NextResponse> {
  // Rule 1 comes first, before the session read and before the body.
  if (!sameOrigin(req)) return refuse("cross_site", 403);
  let path = act === "check" ? "/agent-access/box-tokens/check" : "/agent-access/box-tokens";
  if (act === "rotate") {
    const bad = refuseBadSegment(id);
    if (bad) return bad;
    path = `/agent-access/box-tokens/${encodeURIComponent(id ?? "")}/rotate`;
  }
  const body = await pickBody(req, act);
  if (body instanceof NextResponse) return body;
  return relay(path, body);
}

/** "Revoke all box tokens", for an admin. Same origin only, and no body. */
export async function relayRevokeAll(req: NextRequest): Promise<NextResponse> {
  if (!sameOrigin(req)) return refuse("cross_site", 403);
  return relay("/agent-access/box-tokens/revoke-all", undefined);
}
