/**
 * POST /api/shell/feed/state — what the member did with some My Day cards
 * (`my_day_feed.md` §6.8, WS-58 MD-2a).
 *
 * Proxies the gateway's `POST /shell/feed/state`: seen, snooze, dismiss,
 * "Not useful" and `acted`. The gateway computes each snooze time and each
 * visit. The client sends only what happened.
 *
 * The identity is the session's. The body names no member and no tenant
 * (R11), and the gateway refuses a key it does not know with 422. This
 * route passes the body on as sent, and adds nothing to it.
 *
 * A write that cannot reach the gateway answers 502, never a 200. The
 * client then keeps the batch and sends it again.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** 50 items of about 150 bytes each (§6.8). The cap stops a body the gateway would refuse anyway. */
const MAX_BODY = 16_384;

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await req.text();
  if (body.length > MAX_BODY) {
    return NextResponse.json({ error: "state_too_large" }, { status: 413 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/feed/state`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(6_000),
    });
    if (res.ok) return NextResponse.json(await res.json());
    // A refusal keeps its status, so the client can tell "bad body" from
    // "not reachable".
    return NextResponse.json({ error: "state_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "state_unavailable" }, { status: 502 });
  }
}
