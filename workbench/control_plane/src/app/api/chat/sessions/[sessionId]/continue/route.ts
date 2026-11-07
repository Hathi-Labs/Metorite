/**
 * POST /api/chat/sessions/[sessionId]/continue — open a fresh chat that
 * continues a held chat (`one_assistant_context.md` §9, WT-2).
 *
 * The gateway answers 200 with `{session_id, text}`, 404 while the switch is
 * off or the chat is not open to the member, 409 when the chat holds no message
 * the member typed, and 503 on a fault. The request has no body: the server
 * names the member, the org and the words (R5). The status passes through.
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
      `${GATEWAY_URL}/chat/sessions/${encodeURIComponent(sessionId)}/continue`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(15_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
