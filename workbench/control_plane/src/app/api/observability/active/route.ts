/**
 * GET /api/observability/active
 *
 * Proxies GET /observability/active to the FastAPI gateway — the agent runs
 * currently in flight (the "running right now" panel). Degrades to an empty
 * list rather than erroring so the panel stays quiet when the gateway is down.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/observability/active`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) {
      return NextResponse.json({ runs: [], count: 0 }, { status: 200 });
    }
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ runs: [], count: 0 }, { status: 200 });
  }
}
