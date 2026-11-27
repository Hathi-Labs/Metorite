/**
 * GET  /api/chat/topics — the topics that the member may use (CO-2b)
 * POST /api/chat/topics — a new private topic of the member
 *
 * `chat_organization.md` §6 (D163.1, D163.2). The gateway lists the company
 * topics and the member's own topics, and never a topic of a colleague. A new
 * topic starts private. The topic chip of the chat reads the list and makes a
 * topic here, then moves the chat through
 * `/api/chat/sessions/[sessionId]/topic`.
 *
 * The POST body is `{"name": "<name>"}`. The BFF passes it through as it
 * came, so the gateway is the one judge of it: 422 for a bad name or for any
 * other key, and 409 for a name that the member holds already. Each route
 * answers 404 while `CHAT_TOPICS_ORGS` leaves the org out. The status passes
 * through. The server names the member and the org (R5).
 *
 * Each handler names its gateway path as a literal, so the chat parity fence
 * reads it (`tests/unit/_chat_parity_reader.py`).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

async function answer(res: Response): Promise<NextResponse> {
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/chat/topics`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    return answer(res);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const body = await req.text();
    const res = await gatewayFetch(`${GATEWAY_URL}/chat/topics`, {
      method: "POST",
      headers: { ...(await gatewayHeaders()), "Content-Type": "application/json" },
      body,
      signal: AbortSignal.timeout(5_000),
    });
    return answer(res);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
