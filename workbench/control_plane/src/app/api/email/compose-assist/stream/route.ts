/**
 * POST /api/email/compose-assist/stream
 *
 * Dedicated SSE proxy for the composer's "Draft with AI" progress stream. The
 * generic /api/email/[...path] proxy buffers POST bodies as JSON and would
 * break streaming, so this more-specific route passes the gateway's
 * text/event-stream body straight through (mirrors the note-taker events
 * proxy). Auth rides the Next session cookie; the internal token is attached
 * server-side.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await req.text();
  let upstream: Response;
  try {
    upstream = await gatewayFetch(`${GATEWAY_URL}/email/compose-assist/stream`, {
      method: "POST",
      headers: await gatewayHeaders(),
      body,
      // Abort the upstream draft when the composer goes away — no orphaned
      // LLM spend for a closed tab.
      signal: req.signal,
    });
  } catch {
    return new Response(
      'data: {"type":"error","error":"upstream unavailable"}\n\n',
      { status: 200, headers: { "Content-Type": "text/event-stream" } }
    );
  }
  if (!upstream.ok || !upstream.body) {
    return new Response(
      `data: {"type":"error","error":"upstream error ${upstream.status}"}\n\n`,
      { status: 200, headers: { "Content-Type": "text/event-stream" } }
    );
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
