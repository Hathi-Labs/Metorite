/**
 * GET /api/notes/meetings/[id]/events
 *
 * Dedicated SSE proxy for the note-taker pipeline progress stream. The generic
 * /api/notes/[...path] proxy buffers JSON and would break streaming, so this
 * more-specific route passes the gateway's text/event-stream body straight
 * through (mirrors the observability activity-stream proxy). EventSource can't
 * set headers, so auth rides the Next session cookie and the internal token is
 * attached server-side.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> }
): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { id } = await ctx.params;
  let upstream: Response;
  try {
    upstream = await gatewayFetch(
      `${GATEWAY_URL}/notes/meetings/${encodeURIComponent(id)}/events`,
      { headers: await gatewayHeaders(), signal: req.signal }
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
