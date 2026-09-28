/**
 * GET /api/workflows/runs/[runId]/stream — live run events (SSE relay).
 *
 * Same shape as /api/observability/activity/stream: EventSource cannot send
 * custom headers, so auth rides the Next session cookie here; the internal
 * gateway token + user identity are attached server-side. Degraded upstreams
 * return 200 with an SSE comment (never a non-SSE error body), and the client
 * disconnect signal tears down the gateway stream.
 */
import { NextRequest } from "next/server";
import { GATEWAY_URL, currentIdentity, gatewayHeaders, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ runId: string }> }
): Promise<Response> {
  // Kept SSE-shaped rather than using requireIdentity's JSON 401: EventSource
  // surfaces a non-SSE body as an opaque connection error.
  if (!(await currentIdentity())) {
    return new Response('data: {"error":"unauthorized"}\n\n', {
      status: 401,
      headers: { "Content-Type": "text/event-stream" },
    });
  }
  const { runId } = await ctx.params;
  let upstream: Response;
  try {
    upstream = await gatewayFetch(
      `${GATEWAY_URL}/workflows/runs/${encodeURIComponent(runId)}/stream`,
      {
        headers: await gatewayHeaders(),
        signal: req.signal, // client disconnect → cancel the gateway stream
      }
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
