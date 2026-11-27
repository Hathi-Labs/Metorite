/**
 * GET /api/shell/waiting — what waits on the member (WS-AP AP-2a).
 *
 * Proxies the gateway's `GET /shell/waiting` (`waiting_on_me.md` §7.3). The
 * page `/waiting` reads it. The same shape as `needs/route.ts`:
 *
 * - The identity is the session's. The request carries no tenant and no
 *   member (R5). The only value passed on is `direction=for_you`, fixed
 *   here: AP-2a lists "For you" only, and nothing from the request reaches
 *   the gateway.
 * - A refusal keeps its status. A 404 means `WAITING_ORGS` does not hold
 *   the org, and the page shows its off state, never "Nothing waits on you".
 * - A failure answers 502, never an empty list, because an empty list is a
 *   claim about the member's day.
 *
 * Fence: `route.test.ts`.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/waiting?direction=for_you`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      // The gateway's whole budget is 4 s (TOTAL_BUDGET_S), plus the trip.
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    if (res.ok) return NextResponse.json(await res.json());
    return NextResponse.json({ error: "waiting_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "waiting_unavailable" }, { status: 502 });
  }
}
