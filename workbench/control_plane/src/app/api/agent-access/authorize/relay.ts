/**
 * The shared half of the two consent BFF routes (WS-55 EA-4a). Server only.
 *
 * Spec: `project-docs/specs/external_agent_access.md` §10.5.
 *
 *     POST /api/agent-access/authorize/check    → gateway /agent-access/authorize/check
 *     POST /api/agent-access/authorize/approve  → gateway /agent-access/authorize/approve
 *
 * The consent page at `/connect/authorize` calls these as the signed-in
 * member. Three rules, each with a test in `relay.test.ts`:
 *
 * 1. ⚠️ **Same origin only.** A request whose `Sec-Fetch-Site` is not
 *    `same-origin` gets 403, and so does a request with no such header
 *    (`sameOrigin` in `lib/accountsServer.ts`). This is the CSRF lock of the
 *    consent flow. The gateway cannot do it, because it sees only this
 *    route's server call. `SameSite=Lax` on the session cookie is the second
 *    lock (`sessionCookie.test.ts`).
 * 2. ⚠️ **Never a 3xx.** The page navigates itself with
 *    `window.location.assign(redirect_to)`. This route does not follow a
 *    redirect from the gateway (`redirect: "manual"`), and it turns one into
 *    a 502. So a browser `fetch` can never be bounced to the agent's address
 *    with a code on it.
 * 3. **The body passes as sent.** The member and the organization come from
 *    the session at the gateway (R11). The gateway refuses an unknown key, so
 *    a body that names a member or an org is a 422 there.
 *
 * Every answer carries `Cache-Control: no-store`, because an approval carries
 * a code. A gateway 503 keeps its `Retry-After`.
 */
import { NextResponse, type NextRequest } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export type ConsentAction = "check" | "approve";

/** An authorize request and its choices are well under this. */
export const MAX_CONSENT_BODY = 16_384;

/** The gateway may fetch the client's metadata document (5 s) first. */
const GATEWAY_TIMEOUT_MS = 15_000;

function answer(body: string, status: number, retryAfter?: string | null): NextResponse {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  };
  if (retryAfter) headers["Retry-After"] = retryAfter;
  return new NextResponse(body, { status, headers });
}

function refuse(error: string, status: number): NextResponse {
  return answer(JSON.stringify({ error }), status);
}

/** True for any answer that would move the browser: a 3xx, or an opaque one. */
export function isRedirectAnswer(res: Response): boolean {
  return res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400);
}

export async function relayConsent(req: NextRequest, action: ConsentAction): Promise<NextResponse> {
  // Rule 1 comes first, before the session read and before the body.
  if (!sameOrigin(req)) return refuse("cross_site", 403);
  const me = await requireIdentity();
  if (me instanceof NextResponse) return refuse("unauthenticated", 401);
  const body = await req.text();
  if (body.length > MAX_CONSENT_BODY) return refuse("too_large", 413);

  let res: Response;
  try {
    res = await gatewayFetch(`${GATEWAY_URL}/agent-access/authorize/${action}`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: body || "{}",
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(GATEWAY_TIMEOUT_MS),
    });
  } catch {
    return refuse("unavailable", 502);
  }
  // Rule 2.
  if (isRedirectAnswer(res)) return refuse("bad_gateway", 502);
  return answer(await res.text(), res.status, res.headers.get("retry-after"));
}
