/**
 * POST /api/chat/sessions/[sessionId]/reminder-open — a tap on a reminder
 * link opens the chat (WS-66 RM-4, `calendar_events_reminders.md` §5.7).
 *
 * The body the gateway gets is `{reminder_id}` and nothing else: the server
 * reads the member's own row and builds every word (D-PM-39). It answers 200
 * with `{posted, message_id, preview, reminder, context}`, or 204 for a
 * shared room. A 204 has no body, so it is relayed as one and never read as
 * JSON.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const sent = (await req.json().catch(() => null)) as { reminder_id?: unknown } | null;
    const reminderId = typeof sent?.reminder_id === "string" ? sent.reminder_id : "";
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${encodeURIComponent(sessionId)}/reminder-open`,
      {
        method: "POST",
        headers: { ...(await gatewayHeaders()), "Content-Type": "application/json" },
        body: JSON.stringify({ reminder_id: reminderId }),
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
