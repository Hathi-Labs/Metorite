/**
 * GET /api/observability/cost
 *
 * Proxies GET /observability/cost to the FastAPI gateway — the daily LLM cost
 * rollup (per-day totals + by-model + by-source), in USD. Powers the cost view.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const EMPTY = { days: [], by_model: {}, by_source: {}, totals: { cost: 0, tokens: 0, calls: 0 } };

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const days = req.nextUrl.searchParams.get("days") ?? "7";
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/observability/cost?days=${encodeURIComponent(days)}`,
      { headers: await gatewayHeaders(), signal: AbortSignal.timeout(5_000) },
    );
    if (!res.ok) return NextResponse.json(EMPTY, { status: 200 });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json(EMPTY, { status: 200 });
  }
}
