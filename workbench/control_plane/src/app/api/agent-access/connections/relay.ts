/**
 * The shared half of the four agent-connection BFF routes (WS-55 EA-4b).
 * Server only.
 *
 * Spec: `project-docs/specs/external_agent_access.md` §5.4 and §15 EA-4b.
 *
 *     GET    /api/agent-access/connections            → gateway GET    /agent-access/connections
 *     DELETE /api/agent-access/connections/<id>       → gateway DELETE /agent-access/connections/<id>
 *     GET    /api/integrations/agent-connections      → gateway GET    /integrations/agent-connections
 *     DELETE /api/integrations/agent-connections/<id> → gateway DELETE /integrations/agent-connections/<id>
 *
 * Three rules, each with a test in `relay.test.ts`:
 *
 * 1. ⚠️ **A DELETE is same origin only.** A DELETE whose `Sec-Fetch-Site`
 *    is not `same-origin` gets 403, and so does one with no such header
 *    (`sameOrigin` in `lib/accountsServer.ts`, as the consent relay does). The
 *    gateway sees only this route's server call, so it cannot check that.
 * 2. **The member and the organization come from the session** at the
 *    gateway (R11). The request carries only the id, in the path, through
 *    `refuseBadSegment`.
 * 3. **Never a 3xx, and never cached.** The relay does not follow a gateway
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

const NO_STORE = { "Cache-Control": "no-store" };

function refuse(error: string, status: number): NextResponse {
  return NextResponse.json({ error }, { status, headers: NO_STORE });
}

function isRedirectAnswer(res: Response): boolean {
  return res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400);
}

async function relay(path: string, method: "GET" | "DELETE"): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return refuse("unauthenticated", 401);
  let headers: Record<string, string>;
  try {
    headers = await gatewayHeaders();
  } catch (err) {
    if (err instanceof NoIdentityError) return refuse("unauthenticated", 401);
    throw err;
  }
  let res: Response;
  try {
    res = await gatewayFetch(`${GATEWAY_URL}${path}`, {
      method,
      headers,
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
