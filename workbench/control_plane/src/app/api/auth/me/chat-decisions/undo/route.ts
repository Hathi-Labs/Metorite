/**
 * POST /api/auth/me/chat-decisions/undo — take back one kept decision of a
 * chat (HRD-12b). Proxies the gateway's `/auth/me/chat-decisions/undo`.
 *
 * The body is `{ session_id, id }` and nothing else. The relay forwards those
 * two fields only, so a member, a tenant or any other key never reaches the
 * gateway from the browser (R5, R11).
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";
import { ITEM_ID, SESSION_ID } from "@/lib/chatDecisions";

export const dynamic = "force-dynamic";

/** The body is under 200 bytes. The cap stops a body the gateway would refuse. */
const MAX_BODY = 1_024;

export async function POST(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const text = await req.text();
  if (text.length > MAX_BODY) return NextResponse.json({ detail: "body_too_large" }, { status: 413 });
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return NextResponse.json({ detail: "bad_body" }, { status: 422 });
  }
  const { session_id: sessionId, id } = (parsed ?? {}) as Record<string, unknown>;
  if (typeof sessionId !== "string" || !SESSION_ID.test(sessionId) || typeof id !== "string" || !ITEM_ID.test(id)) {
    return NextResponse.json({ detail: "bad_body" }, { status: 422 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/chat-decisions/undo`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ session_id: sessionId, id }),
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "chat_decisions_unavailable" }, { status: 502 });
  }
}
