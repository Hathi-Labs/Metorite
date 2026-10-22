/**
 * POST /api/chat/sessions/[sessionId]/brief — the day's brief as the first
 * assistant turn of a fresh chat (WS-62 CF-5, `chat_first_surface.md` §4.6).
 *
 * The gateway answers 200 with `{posted, message_id, preview}` when it wrote
 * the row, and 204 when there is nothing to post. A 204 has no body, so it is
 * relayed as one and never read as JSON.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${encodeURIComponent(sessionId)}/brief`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        // The feed read behind it can take a few seconds on a cold morning.
        signal: AbortSignal.timeout(15_000),
      },
    );
    if (res.status === 204) return new NextResponse(null, { status: 204 });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
