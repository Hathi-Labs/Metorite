/**
 * GET /api/whatsapp/calls/[callId]/recording
 *
 * Streams a WhatsApp call's recorded audio so an <audio> element can play it.
 *
 * This needs its own route rather than riding the /api/whatsapp/[...path]
 * catch-all: that proxy parses every reply as JSON, which would quietly turn a
 * WAV into an empty object. A more specific route wins over the catch-all, so
 * adding this file is enough to take the binary path.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ callId: string }> };

export async function GET(req: NextRequest, ctx: Ctx): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  const { callId } = await ctx.params;
  const accountId = req.nextUrl.searchParams.get("account_id") ?? "";
  if (!accountId) {
    return NextResponse.json({ detail: "account_id required" }, { status: 400 });
  }

  try {
    const upstream = `${GATEWAY_URL}/whatsapp/calls/${encodeURIComponent(
      callId
    )}/recording?account_id=${encodeURIComponent(accountId)}`;
    const res = await gatewayFetch(upstream, {
      headers: await gatewayHeaders(),
      // Generous: a long call's WAV is a few MB and the gateway pulls it from
      // the bridge before handing it on.
      signal: AbortSignal.timeout(60_000),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      return NextResponse.json(
        { detail: detail.slice(0, 300) || `HTTP ${res.status}` },
        { status: res.status }
      );
    }
    return new Response(res.body, {
      status: 200,
      headers: {
        "Content-Type": "audio/wav",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json({ detail: "gateway unreachable" }, { status: 502 });
  }
}
