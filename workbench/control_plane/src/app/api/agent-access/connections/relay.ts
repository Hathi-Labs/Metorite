/**
 * The shared half of the five agent-connection BFF routes (WS-55 EA-4b,
 * EA-17c-1). Server only.
 *
 * Spec: `project-docs/specs/external_agent_access.md` §5.4, §10.8 and §15
 * EA-4b and EA-17c-1.
 *
 *     GET    /api/agent-access/connections            → gateway GET    /agent-access/connections
 *     DELETE /api/agent-access/connections/<id>       → gateway DELETE /agent-access/connections/<id>
 *     PATCH  /api/agent-access/connections/<id>       → gateway PATCH  /agent-access/connections/<id>
 *     GET    /api/integrations/agent-connections      → gateway GET    /integrations/agent-connections
 *     DELETE /api/integrations/agent-connections/<id> → gateway DELETE /integrations/agent-connections/<id>
 *
 * Four rules, each with a test in `relay.test.ts`:
 *
 * 1. ⚠️ **A DELETE and a PATCH are same origin only.** A write whose
 *    `Sec-Fetch-Site` is not `same-origin` gets 403, and so does one with no
 *    such header (`sameOrigin` in `lib/accountsServer.ts`, as the consent
 *    relay does). The gateway sees only this route's server call, so it
 *    cannot check that.
 * 2. **The member and the organization come from the session** at the
 *    gateway (R11). The request carries only the id, in the path, through
 *    `refuseBadSegment`.
 * 3. **A PATCH body holds the four fields of a grant, and nothing else**
 *    (EA-17c-1, D-EA-13). The relay builds the body that it sends from
 *    `scopes`, `pinned_projects`, `left_out_projects` and `include_personal`.
 *    Any other field, such as an org or a member, never reaches the gateway.
 *    A PATCH with no `Content-Length` gets 411. A declared length over
 *    `MAX_CHANGE_BODY` gets 413 before the read, and so does a longer body
 *    after it. A body that is not a JSON object gets 400. The gateway checks
 *    each value.
 * 4. **Never a 3xx, and never cached.** The relay does not follow a gateway
 *    redirect, and turns one into a 502. Each answer carries
 *    `Cache-Control: no-store`.
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

/** The two gateway lists: the member's own, and the org's for an admin. */
export type AgentList = "/agent-access/connections" | "/integrations/agent-connections";

const GATEWAY_TIMEOUT_MS = 10_000;

/** A change body is far under this: two lists of 50 ids and the scopes. */
export const MAX_CHANGE_BODY = 16_384;

/** The four fields of a grant that a PATCH may send (rule 3). */
const CHANGE_FIELDS = ["scopes", "pinned_projects", "left_out_projects", "include_personal"] as const;

const NO_STORE = { "Cache-Control": "no-store" };

function refuse(error: string, status: number): NextResponse {
  return NextResponse.json({ error }, { status, headers: NO_STORE });
}

function isRedirectAnswer(res: Response): boolean {
  return res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400);
}

async function relay(path: string, method: "GET" | "DELETE" | "PATCH", body?: string): Promise<NextResponse> {
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
  if (res.status === 204) return new NextResponse(null, { status: 204, headers: NO_STORE });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json", ...NO_STORE },
  });
}

/** GET one of the two lists. A GET changes nothing, so it needs no origin check. */
export async function relayList(list: AgentList): Promise<NextResponse> {
  return relay(list, "GET");
}

/** DELETE one connection of a list. Same origin only (rule 1). */
export async function relayRevoke(req: NextRequest, list: AgentList, id: string): Promise<NextResponse> {
  // Rule 1 comes first, before the session read.
  if (!sameOrigin(req)) return refuse("cross_site", 403);
  const bad = refuseBadSegment(id);
  if (bad) return bad;
  return relay(`${list}/${encodeURIComponent(id)}`, "DELETE");
}

/** The `Content-Length` of *req* as a whole number, or `null` with none. */
function declaredLength(req: NextRequest): number | null {
  const value = req.headers.get("content-length");
  if (value === null || !/^[0-9]{1,9}$/.test(value.trim())) return null;
  return Number(value.trim());
}

/**
 * PATCH the grant of one of the member's own connections (EA-17c-1). Same
 * origin only (rule 1), and the body that leaves holds the four fields of a
 * grant only (rule 3).
 */
export async function relayChange(req: NextRequest, id: string): Promise<NextResponse> {
  // Rule 1 comes first, before the session read and before the body.
  if (!sameOrigin(req)) return refuse("cross_site", 403);
  const bad = refuseBadSegment(id);
  if (bad) return bad;
  // The declared length bounds the read before it starts. A body with no
  // length is 411, and one past the cap is 413. The cap after the read stays
  // as a second check.
  const declared = declaredLength(req);
  if (declared === null) return refuse("length_required", 411);
  if (declared > MAX_CHANGE_BODY) return refuse("too_large", 413);
  const raw = await req.text();
  if (raw.length > MAX_CHANGE_BODY) return refuse("too_large", 413);
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return refuse("bad_body", 400);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return refuse("bad_body", 400);
  }
  // Rule 3: own keys of the four fields only. A missing field stays missing,
  // so the gateway answers its 422 and never reads it as empty.
  const given = parsed as Record<string, unknown>;
  const grant: Record<string, unknown> = {};
  for (const field of CHANGE_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(given, field)) grant[field] = given[field];
  }
  return relay(`/agent-access/connections/${encodeURIComponent(id)}`, "PATCH", JSON.stringify(grant));
}
