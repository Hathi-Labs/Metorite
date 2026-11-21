/**
 * PUT /api/chat/sessions/[sessionId]/topic — move a chat into a topic, for
 * the member (CO-2b)
 *
 * `chat_organization.md` §6 (D163). The topic of a chat is the member's own,
 * and the automatic pass never writes over it. The body is
 * `{"topic_id": "<id>"}`, or `{"topic_id": null}` for "No topic". The BFF
 * passes the body through as it came, so the gateway is the one judge of it:
 * 422 for a body that names any other key, such as a member or an org. The
 * server names the member and the org (R5).
 *
 * The gateway answers 200 with `{topicId, topicName}`. It answers 404 while
 * `CHAT_TOPICS_ORGS` leaves the org out, 404 "Topic not found" for a topic
 * that the member may not use, and 404 "Session not found" for a chat that
 * is not open to the member. The status passes through.
 *
 * The handler names its gateway path as a literal, so the chat parity fence
 * reads it (`tests/unit/_chat_parity_reader.py`).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const refused = refuseUnsafePath([sessionId]);
    if (refused) return refused;
    const body = await req.text();
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${encodeURIComponent(sessionId)}/topic`,
      {
        method: "PUT",
        headers: { ...(await gatewayHeaders()), "Content-Type": "application/json" },
        body,
        signal: AbortSignal.timeout(5_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
