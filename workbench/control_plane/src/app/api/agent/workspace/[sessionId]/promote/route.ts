/**
 * POST /api/agent/workspace/[sessionId]/promote
 *   — move an inputs/ file into agent-data/ (durable, prompt-shaping storage).
 *   Body: { path: "inputs/x.pdf", dest?: "agent-data/x.pdf" }
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const body = await req.json();
    const res = await gatewayFetch(`${GATEWAY_URL}/agent/workspace/${sessionId}/promote`, {
      method: "POST",
      headers: { ...(await gatewayHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
