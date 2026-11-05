/**
 * The shared half of the three app-request BFF routes (WS-MCP MCP-P1b-1).
 * Server only.
 *
 * Spec: `project-docs/specs/external_agent_access.md` §19.5, "The member routes".
 *
 *     GET  /api/agent-access/requests                → gateway GET  /agent-access/requests
 *     GET  /api/agent-access/requests/<id>           → gateway GET  /agent-access/requests/<id>
 *     POST /api/agent-access/requests/<id>/decision  → gateway POST /agent-access/requests/<id>/decision
 *
 * Four rules, each with a test in `relay.test.ts`:
 *
 * 1. ⚠️ **A decision is same origin only.** A POST whose `Sec-Fetch-Site` is
 *    not `same-origin` gets 403, and so does one with no such header
 *    (`sameOrigin` in `lib/accountsServer.ts`). The gateway sees only this
 *    route's server call, so it cannot check that.
 * 2. **The body holds the decision and the key, and nothing else.** The
 *    relay builds the body that it sends from those two fields. Any other
 *    field, such as an org or a member, never reaches the gateway, which
 *    takes both from the session (R11).
 * 3. **The id is one safe segment** (`refuseBadSegment`), before any fetch.
 * 4. **Never a 3xx, and never cached.** The relay does not follow a gateway
 *    redirect, and turns one into a 502. Each answer says `no-store`.
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

const GATEWAY_TIMEOUT_MS = 10_000;

/** A decision body is far under this. */
export const MAX_DECISION_BODY = 1_024;

const NO_STORE = { "Cache-Control": "no-store" };

const DECISIONS = new Set(["approve", "decline"]);

function refuse(error: string, status: number): NextResponse {
  return NextResponse.json({ error }, { status, headers: NO_STORE });
}

function isRedirectAnswer(res: Response): boolean {
  return res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400);
}

async function relay(path: string, method: "GET" | "POST", body?: string): Promise<NextResponse> {
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
      method,
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
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json", ...NO_STORE },
  });
}

/** GET the requests that wait for the member. */
export async function relayList(): Promise<NextResponse> {
  return relay("/agent-access/requests", "GET");
}

/** GET one request of the member. */
export async function relayOne(id: string): Promise<NextResponse> {
  const bad = refuseBadSegment(id);
  if (bad) return bad;
  return relay(`/agent-access/requests/${encodeURIComponent(id)}`, "GET");
}

/** POST the member's decision. Same origin only (rule 1). */
export async function relayDecision(req: NextRequest, id: string): Promise<NextResponse> {
  // Rule 1 comes first, before the session read and before the body.
  if (!sameOrigin(req)) return refuse("cross_site", 403);
  const bad = refuseBadSegment(id);
  if (bad) return bad;
  const raw = await req.text();
  if (raw.length > MAX_DECISION_BODY) return refuse("too_large", 413);
  let parsed: { decision?: unknown; idempotency_key?: unknown };
  try {
    parsed = JSON.parse(raw || "{}") as typeof parsed;
  } catch {
    return refuse("bad_body", 400);
  }
  if (
    typeof parsed.decision !== "string" ||
    !DECISIONS.has(parsed.decision) ||
    typeof parsed.idempotency_key !== "string" ||
    !parsed.idempotency_key
  ) {
    return refuse("bad_body", 400);
  }
  // Rule 2: the body that leaves holds these two fields only.
  const body = JSON.stringify({ decision: parsed.decision, idempotency_key: parsed.idempotency_key });
  return relay(`/agent-access/requests/${encodeURIComponent(id)}/decision`, "POST", body);
}
