/**
 * POST /api/integrations/mcp/[name]/test
 *
 * Proxies to POST /integrations/mcp/{name}/test on the FastAPI gateway. The
 * gateway tests with the headers it stored, so the browser sends no body.
 */

import { NextRequest, NextResponse } from "next/server";
import { refuseBadSegment } from "@/app/api/integrations/safeSegment";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;
  const bad = refuseBadSegment(name);
  if (bad) return bad;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/mcp/${encodeURIComponent(name)}/test`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(20_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
