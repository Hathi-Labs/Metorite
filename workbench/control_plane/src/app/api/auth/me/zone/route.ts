/**
 * GET and PUT /api/auth/me/zone — the member's saved time zone (WS-66 RX-7).
 *
 * Proxies the gateway's `/auth/me/zone`. That route needs a signed-in member
 * and no app feature, so a member without `feature:tasks` can still save the
 * zone that the morning summary and "tomorrow at 5" read. `/api/tasks/settings`
 * needs `feature:tasks` and stays the route of the Calendar settings.
 *
 * ⚠️ A failure is an error, never a default zone. An unreachable gateway
 * answers 502, and the client keeps the zone it had.
 *
 * The identity is the session's. The body names no member and no tenant (R5),
 * and the gateway refuses a key it does not know.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** A zone body is under 100 bytes. The cap stops a body the gateway would refuse anyway. */
const MAX_BODY = 1_024;

async function relay(res: Response): Promise<NextResponse> {
  if (res.ok) return NextResponse.json(await res.json());
  // A refusal keeps its status, so the client can tell "bad zone" from "not reachable".
  return NextResponse.json({ error: "zone_unavailable" }, { status: res.status });
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/zone`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "zone_unavailable" }, { status: 502 });
  }
}

export async function PUT(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await req.text();
  if (body.length > MAX_BODY) {
    return NextResponse.json({ error: "zone_too_large" }, { status: 413 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/zone`, {
      method: "PUT",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: body || "{}",
      cache: "no-store",
      signal: AbortSignal.timeout(6_000),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "zone_unavailable" }, { status: 502 });
  }
}
