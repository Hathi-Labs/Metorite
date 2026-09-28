/**
 * GET /api/chat/sessions/[sessionId]/room-stream
 *
 * Proxies the gateway's Server-Sent Events room feed
 * (GET /chat/sessions/{id}/room-stream) to the browser. EventSource cannot send
 * custom headers, so auth rides the Next session cookie here; the internal
 * gateway token + resolved user role are attached server-side. The `since`
 * cursor is forwarded so a reconnecting client replays only what it missed.
 *
 * The upstream fetch is bound to the request's abort signal so a client
 * disconnect tears down the gateway connection instead of leaking it.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { sessionId } = await params;
  const since = req.nextUrl.searchParams.get("since") ?? "0";
  let upstream: Response;
  try {
    upstream = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${sessionId}/room-stream?since=${encodeURIComponent(since)}`,
      {
        headers: await gatewayHeaders(),
        signal: req.signal, // client disconnect → cancel the gateway stream
      },
    );
  } catch {
    return new Response(": upstream unavailable\n\n", {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }
  if (!upstream.ok || !upstream.body) {
    return new Response(": upstream error\n\n", {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
