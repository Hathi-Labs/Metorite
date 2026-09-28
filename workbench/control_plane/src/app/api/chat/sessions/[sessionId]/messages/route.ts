/**
 * GET  /api/chat/sessions/[sessionId]/messages   — load message history
 * POST /api/chat/sessions/[sessionId]/messages   — upsert a batch of messages
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
    // Forward pagination params for windowed lazy-loading:
    //   ?limit=N        — return only the most recent N messages
    //   ?before=<ms>    — return only messages older than this timestamp_ms
    //                     (cursor for loading older history on scroll-up)
    const qs = new URLSearchParams();
    const limit = req.nextUrl.searchParams.get("limit");
    const before = req.nextUrl.searchParams.get("before");
    if (limit) qs.set("limit", limit);
    if (before) qs.set("before", before);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${sessionId}/messages${suffix}`,
      {
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(8_000),
      },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const body = await req.json();
    const res = await gatewayFetch(`${GATEWAY_URL}/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
