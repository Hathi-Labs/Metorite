/**
 * GET /api/agent/workspace/[sessionId]/history?path=&limit=
 *   — version history of an agent's tracked files (blob store).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const { searchParams } = new URL(req.url);
    const qs = new URLSearchParams();
    const path = searchParams.get("path");
    const limit = searchParams.get("limit");
    if (path) qs.set("path", path);
    if (limit) qs.set("limit", limit);
    const res = await gatewayFetch(
      `${GATEWAY_URL}/agent/workspace/${sessionId}/history?${qs.toString()}`,
      { headers: await gatewayHeaders(), signal: AbortSignal.timeout(10_000) },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
