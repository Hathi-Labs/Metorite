/**
 * POST /api/agent/cancel
 *
 * Actually stops a running agent (vs. just dropping the SSE connection, which
 * leaves the agent running detached in the background).  Proxies to the gateway
 * `POST /agent/run/{threadId}/cancel`, which cancels the background task,
 * marks the thread inactive, and pushes a terminal RUN_FINISHED event.
 *
 * Body: { threadId: string }
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  let threadId = "";
  try {
    const body = (await req.json()) as { threadId?: string };
    threadId = (body.threadId ?? "").trim();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (!threadId) {
    return new Response(JSON.stringify({ error: "threadId is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const headers = await gatewayHeaders({ "Content-Type": "application/json" });

  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/agent/run/${encodeURIComponent(threadId)}/cancel`,
      { method: "POST", headers, signal: AbortSignal.timeout(8_000) },
    );
    const text = await res.text();
    return new Response(text, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(
      JSON.stringify({ error: `Gateway unreachable: ${String(err)}` }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
}
