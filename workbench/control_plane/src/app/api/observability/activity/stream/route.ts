/**
 * GET /api/observability/activity/stream
 *
 * Proxies the gateway's Server-Sent Events activity feed
 * (GET /observability/activity/stream) to the browser. EventSource cannot send
 * custom headers, so auth rides the Next session cookie here; the internal
 * gateway token + resolved user role are attached server-side.
 *
 * The upstream fetch is bound to the request's abort signal so a client
 * disconnect tears down the gateway connection instead of leaking it.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  let upstream: Response;
  try {
    upstream = await gatewayFetch(`${GATEWAY_URL}/observability/activity/stream`, {
      headers: await gatewayHeaders(),
      signal: req.signal, // client disconnect → cancel the gateway stream
    });
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
