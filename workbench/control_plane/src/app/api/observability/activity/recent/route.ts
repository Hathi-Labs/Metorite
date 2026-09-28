/**
 * GET /api/observability/activity/recent
 *
 * Proxies GET /observability/activity/recent to the FastAPI gateway — the
 * recent slice of the global activity bus, used to backfill the live feed on
 * first load before the SSE stream takes over.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const limit = req.nextUrl.searchParams.get("limit") ?? "100";
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/observability/activity/recent?limit=${encodeURIComponent(limit)}`,
      { headers: await gatewayHeaders(), signal: AbortSignal.timeout(5_000) },
    );
    if (!res.ok) {
      return NextResponse.json({ events: [], count: 0 }, { status: 200 });
    }
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ events: [], count: 0 }, { status: 200 });
  }
}
