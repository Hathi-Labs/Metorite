/**
 * PATCH  /api/chat/sessions/[sessionId]   — update session title/preview/count
 * DELETE /api/chat/sessions/[sessionId]   — delete session + cascade messages
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const body = await req.json();
    const res = await gatewayFetch(`${GATEWAY_URL}/chat/sessions/${sessionId}`, {
      method: "PATCH",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5_000),
    });
    if (res.status === 204 || res.headers.get("content-length") === "0") {
      return new NextResponse(null, { status: res.status });
    }
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const res = await gatewayFetch(`${GATEWAY_URL}/chat/sessions/${sessionId}`, {
      method: "DELETE",
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    return new NextResponse(null, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
