/**
 * POST /api/chat/sessions/[sessionId]/buddy-hello — the first-visit hello as
 * the first assistant turn of a fresh chat (WS-BUD BUD-1, D177,
 * `onboarding.md` §13).
 *
 * No body goes to the gateway: the tenant and the member come from the
 * session, and the server writes every word. It answers 200 with
 * `{posted, message_id, preview, title}` when it wrote the row, and 204 when
 * the stamp was already taken. A 204 has no body, so it is relayed as one and
 * never read as JSON.
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
      `${GATEWAY_URL}/chat/sessions/${encodeURIComponent(sessionId)}/buddy-hello`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(15_000),
      },
    );
    if (res.status === 204) return new NextResponse(null, { status: 204 });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
