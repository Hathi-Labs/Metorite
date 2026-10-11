/**
 * POST /api/integrations/connections/[id]/disconnect
 *
 * Proxies to the FastAPI gateway. The gateway floors it on
 * `integrations:manage` and answers 409 `member_connection` for a row that a
 * member owns.
 */

import { NextRequest, NextResponse } from "next/server";
import { refuseBadSegment } from "@/app/api/integrations/safeSegment";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { id } = await params;
  const bad = refuseBadSegment(id);
  if (bad) return bad;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/connections/${encodeURIComponent(id)}/disconnect`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(15_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
