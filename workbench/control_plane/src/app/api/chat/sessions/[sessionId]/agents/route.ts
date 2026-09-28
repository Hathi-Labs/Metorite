/**
 * POST /api/chat/sessions/[sessionId]/agents — add an agent to a room
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
    const res = await gatewayFetch(`${GATEWAY_URL}/chat/sessions/${sessionId}/agents`, {
      method: "POST",
      headers: await gatewayHeaders(),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5_000),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
