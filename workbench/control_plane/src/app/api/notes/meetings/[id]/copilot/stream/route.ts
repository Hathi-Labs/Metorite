/**
 * GET /api/notes/meetings/[id]/copilot/stream
 *
 * SSE proxy for the copilot's suggestion stream. Separate from the transcript
 * stream on purpose — the copilot's output is its own channel, so its chatter
 * never mixes into the transcript. The generic /api/notes/[...path] proxy
 * buffers JSON and would break streaming, hence this dedicated route.
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
      `${GATEWAY_URL}/notes/meetings/${encodeURIComponent(id)}/copilot/stream`,
      { headers: await gatewayHeaders(), signal: req.signal }
    );
  } catch {
    // The copilot is additive — a missing stream must never look like an error.
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
