/**
 * GET /api/observability/runs
 *
 * Durable activity history from the gateway (GET /observability/runs → the
 * agent_run store). Powers the History tab and the per-agent drill-down.
 * Forwards agent / status / since_hours / limit filters. Open to any
 * authenticated operator; returns lean metadata rows (no full trace content).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const sp = req.nextUrl.searchParams;
  const qs = new URLSearchParams();
  for (const k of ["agent", "status", "user", "thread_id", "since_hours", "limit"]) {
    const v = sp.get(k);
    if (v) qs.set(k, v);
  }
  try {
    // Durable run history from the observability route (open to any authed
    // operator) — NOT /debug/runs, which is EXECUTIVE-gated and returns full
    // message-content traces. This endpoint returns lean metadata rows only.
    const res = await gatewayFetch(`${GATEWAY_URL}/observability/runs?${qs.toString()}`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(6_000),
    });
    if (!res.ok) return NextResponse.json({ runs: [] }, { status: 200 });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ runs: [] }, { status: 200 });
  }
}
