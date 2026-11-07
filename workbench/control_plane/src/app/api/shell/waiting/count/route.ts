/**
 * GET /api/shell/waiting/count — how many waits the member has (WS-AP AP-2a).
 *
 * Proxies the gateway's `GET /shell/waiting/count` (`waiting_on_me.md` §7.3).
 * The sidebar's Waiting row reads it through `useNavNeeds`. The rules of
 * `../route.ts` hold here too: the session's identity, no request value
 * passed on, a refusal keeps its status (a 404 is the off state), and a
 * failure answers 502, never a count of 0.
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
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/waiting/count`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    if (res.ok) return NextResponse.json(await res.json());
    return NextResponse.json({ error: "waiting_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "waiting_unavailable" }, { status: 502 });
  }
}
