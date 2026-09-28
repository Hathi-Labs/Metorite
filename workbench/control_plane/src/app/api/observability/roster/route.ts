/**
 * GET /api/observability/roster
 *
 * Proxies GET /observability/roster to the FastAPI gateway — every registered
 * agent with its live status (working / idle). Powers the office view's cast.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/observability/roster`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) return NextResponse.json({ agents: [], count: 0 }, { status: 200 });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ agents: [], count: 0 }, { status: 200 });
  }
}
