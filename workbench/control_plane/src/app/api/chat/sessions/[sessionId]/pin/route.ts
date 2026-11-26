/**
 * PUT    /api/chat/sessions/[sessionId]/pin — pin a chat for the member (CO-11)
 * DELETE /api/chat/sessions/[sessionId]/pin — unpin it
 *
 * `chat_organization.md` §18. The pin is the member's own. The gateway
 * answers 200 with `{pinned, pinnedAt}`, 404 while `CHAT_PIN_ORGS` leaves the
 * org out or the chat is not open to the member, 409 for an archived chat,
 * and 403 for a session with no address. The request has no body: the server
 * names the member and the org (R5). The status passes through.
 *
 * Each handler names its gateway path as a literal, so the chat parity fence
 * reads it (`tests/unit/_chat_parity_reader.py`).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

async function answer(res: Response): Promise<NextResponse> {
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function PUT(
  _req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const refused = refuseUnsafePath([sessionId]);
    if (refused) return refused;
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${encodeURIComponent(sessionId)}/pin`,
      { method: "PUT", headers: await gatewayHeaders(), signal: AbortSignal.timeout(5_000) },
    );
    return answer(res);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const refused = refuseUnsafePath([sessionId]);
    if (refused) return refused;
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${encodeURIComponent(sessionId)}/pin`,
      { method: "DELETE", headers: await gatewayHeaders(), signal: AbortSignal.timeout(5_000) },
    );
    return answer(res);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
